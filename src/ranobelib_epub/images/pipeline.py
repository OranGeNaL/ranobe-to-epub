"""Загрузка, сжатие и встраивание иллюстраций (решение 9, задачи 8.1-8.6).

Что установлено по реальным данным: узел `image` ссылается на вложения ключом
`attrs.images[].image` — это UUID **без расширения**, а `attachments[]` содержит
`name` (тот же UUID), `extension`, `url` и размеры. Сам файл лежит на origin сайта
(`https://ranobelib.me/uploads/...`) и отдаётся только с `Referer` и браузерным
`User-Agent`: без них CDN отвечает 403, а хост API (`api.cdnlibs.org/api/uploads/...`)
на эти пути отвечает 404.

Измеренный профиль сжатия: PNG 2210×1582 RGBA (5014 КБ) → JPEG 1280 px, quality 80,
`optimize` + `progressive` — около 229 КБ. WebP при том же качестве больше (352 КБ) и
хуже поддерживается, поэтому не используется.
"""

from __future__ import annotations

import io
import posixpath
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from PIL import Image

from ..models import Attachment, MissingImage
from .presets import DEFAULT_PRESET, PRESETS

#: Значения по умолчанию равны пресету `medium` — единый источник истины в presets.py.
_MEDIUM = PRESETS[DEFAULT_PRESET]
DEFAULT_MAX_WIDTH = _MEDIUM.max_width
DEFAULT_QUALITY = _MEDIUM.quality
DEFAULT_MAX_MB = _MEDIUM.max_mb


def attachment_key(entry: dict | str) -> str:
    """Ключ вложения из элемента `attrs.images[]`.

    В реальных данных ключ называется `image`; `name` тоже встречается, поэтому
    принимаются оба — иначе одно расхождение схемы молча убирает все иллюстрации.
    """
    if isinstance(entry, str):
        return entry
    return str(entry.get("image") or entry.get("name") or "")


def image_keys(node: dict) -> tuple[str, ...]:
    """Ключи всех картинок узла `image` (их может быть несколько)."""
    attrs = node.get("attrs") or {}
    files = attrs.get("images") or []
    if isinstance(files, (str, dict)):
        files = [files]
    return tuple(key for key in (attachment_key(item) for item in files) if key)


def collect_image_keys(document: dict | None) -> list[str]:
    """Ключи всех картинок документа в порядке следования, без повторов.

    Порядок нужен, чтобы имена файлов в EPUB шли в том же порядке, что и картинки в
    тексте: иначе пересборка с тем же текстом давала бы другой архив.
    """
    keys: list[str] = []
    seen: set[str] = set()

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "image":
            for key in image_keys(node):
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        for child in node.get("content") or []:
            walk(child)

    root = document
    if root and root.get("type") != "doc" and isinstance(root.get("content"), dict):
        root = root["content"]
    walk(root)
    return keys


def absolute_url(attachment: Attachment | dict, origin: str) -> str:
    """Абсолютный URL файла: путь из `attachments` с префиксом origin.

    Уже абсолютный URL не переписывается, чтобы не ломать зеркала и CDN. Origin
    принадлежит сайту-источнику и передаётся явно.
    """
    url = attachment["url"] if isinstance(attachment, dict) else attachment.url
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return f"{origin.rstrip('/')}/{url.lstrip('/')}"


def build_url_from_key(key: str, origin: str, extension: str = "jpg") -> str:
    """Резервный URL, когда у вложения нет `url`.

    Без реального пути к файлу построить его нельзя, поэтому функция честно
    возвращает origin-путь по ключу: вызывающий код решает, годится ли он, или
    картинка считается потерянной.
    """
    if not key:
        return ""
    suffix = f".{extension}" if extension and not key.endswith(f".{extension}") else ""
    return f"{origin.rstrip('/')}/uploads/{key}{suffix}"


def resolve_attachments(chapter: dict | Attachment) -> dict[str, Attachment]:
    """Индекс вложений главы по ключу `name` для O(1)-поиска картинок."""
    if isinstance(chapter, Attachment):
        attachments = [chapter]
    else:
        attachments = [
            Attachment(
                name=item.get("name", ""),
                extension=item.get("extension", ""),
                url=item.get("url", ""),
                width=item.get("width"),
                height=item.get("height"),
            )
            for item in (chapter.get("attachments") or [])
        ]
    return {item.name: item for item in attachments if item.name}


@dataclass(slots=True)
class ImageAsset:
    """Готовое к встраиванию изображение."""

    filename: str
    data: bytes
    source_url: str
    mime: str
    compressed: bool = True


@dataclass(slots=True)
class ImageResult:
    """Итог работы с иллюстрациями главы."""

    assets: list[ImageAsset] = field(default_factory=list)
    missing: list[MissingImage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def by_key(self) -> dict[str, ImageAsset]:
        return {asset.source_url: asset for asset in self.assets}


def flatten_to_white(image: Image.Image) -> Image.Image:
    """Заменяет прозрачность белым через `alpha_composite` (задача 8.3).

    Простое `convert("RGB")` даёт чёрные участки вместо прозрачных, поэтому
    прозрачность накладывается на белый холст, а уже он идёт в JPEG без альфы.
    """
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white, rgba).convert("RGB")
    return image if image.mode == "RGB" else image.convert("RGB")


def compress_image(
    raw: bytes,
    max_width: int = DEFAULT_MAX_WIDTH,
    quality: int = DEFAULT_QUALITY,
    grayscale: bool = False,
) -> ImageAsset:
    """Перекодирует изображение в JPEG заданной ширины и качества (8.2, 8.3).

    Уменьшение только вниз: картинка уже меньше лимита остаётся своего размера.
    При `grayscale=True` результат переводится в оттенки серого — режим `L`
    корректно сохраняется в JPEG.
    """
    with Image.open(io.BytesIO(raw)) as opened:
        opened.load()
        # `copy()` обязателен: без него результат остаётся привязанным к файлу,
        # который закроется на выходе из `with`, и JPEG не сохранится — такое
        # проявлялось только на картинках, которые не пришлось уменьшать.
        image = flatten_to_white(opened).copy()

    if grayscale:
        image = image.convert("L")

    if image.width > max_width:
        height = round(image.height * max_width / image.width)
        image = image.resize((max_width, max(1, height)), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return ImageAsset(
        filename="",
        data=buffer.getvalue(),
        source_url="",
        mime="image/jpeg",
        compressed=True,
    )


def keep_original(raw: bytes) -> ImageAsset:
    """Встраивает исходные байты без перекодирования (`--max-image-mb 0`, 8.4)."""
    mime = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    if raw[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    return ImageAsset(filename="", data=raw, source_url="", mime=mime, compressed=False)


def should_compress(
    size_bytes: int,
    image_format: str | None,
    width: int,
    height: int,
    max_image_mb: float = DEFAULT_MAX_MB,
    max_width: int = DEFAULT_MAX_WIDTH,
    grayscale: bool = False,
    image_mode: str | None = None,
) -> bool:
    """Следует ли перекодировать изображение с такими параметрами.

    Перекодирование нужно, если изображение шире `max_width`, если исходный
    формат не JPEG (PNG и GIF всегда перекодируются в JPEG) или если его размер
    в байтах превышает порог `max_image_mb`. `max_image_mb == 0` отключает сжатие
    целиком (сценарий «Лимит сжатия отключён»), и тогда исходные байты
    сохраняются независимо от формата и размеров. Запрошенные градации серого
    заставляют перекодировать даже небольшие цветные JPEG.
    """
    if max_image_mb <= 0:
        return False
    if grayscale and (image_mode or "").upper() not in {"L", "LA"}:
        return True
    if width > max_width:
        return True
    if (image_format or "").upper() != "JPEG":
        return True
    return size_bytes > max_image_mb * 1024 * 1024


def epub_filename(index: int, mime: str) -> str:
    """Имя файла внутри EPUB: `Images/img_0001.jpg` (задача 8.6)."""
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}.get(mime, "jpg")
    return f"Images/img_{index:04d}.{extension}"


def chapter_image_href(filename: str, chapter_dir: str = "TEXT") -> str:
    """Ссылка на картинку из XHTML главы.

    Главы лежат в `EPUB/TEXT/`, картинки — в `EPUB/Images/`, поэтому в `src`
    нужен относительный путь: абсолютный или «плоский» путь не разрешится, а
    epubcheck считает такой файл отсутствующим.
    """
    return posixpath.relpath(filename, start=chapter_dir)


def assign_filenames(assets: list[ImageAsset]) -> list[ImageAsset]:
    """Проставляет уникальные имена файлов в порядке следования (8.6).

    Имена не пересекаются по построению — сквозная нумерация, — поэтому проверка
    уникальности всегда проходит, а порядок файлов совпадает с порядком глав.
    """
    used: set[str] = set()
    for index, asset in enumerate(assets, start=1):
        candidate = epub_filename(index, asset.mime)
        suffix = 2
        while candidate in used:
            base = PurePosixPath(candidate)
            candidate = str(base.with_name(f"{base.stem}_{suffix}{base.suffix}"))
            suffix += 1
        used.add(candidate)
        asset.filename = candidate
    return assets


PLACEHOLDER_TEMPLATE = "[изображение недоступно: {url}]"


def placeholder(url: str) -> str:
    """Текстовая заглушка вместо недоступной иллюстрации (задача 8.5)."""
    return PLACEHOLDER_TEMPLATE.format(url=url)


def mark_missing(
    url: str, reason: str, chapter_label: str = "", reference: str = ""
) -> MissingImage:
    """Запись о недоступном изображении для отчёта (8.5)."""
    return MissingImage(chapter_label=chapter_label, reference=reference, url=url, reason=reason)


def filter_and_compress(
    raw: bytes,
    max_image_mb: float = DEFAULT_MAX_MB,
    max_width: int = DEFAULT_MAX_WIDTH,
    quality: int = DEFAULT_QUALITY,
    grayscale: bool = False,
) -> ImageAsset:
    """Точка входа: решает по заголовку, сжимать ли, и возвращает готовый актив."""
    with Image.open(io.BytesIO(raw)) as opened:
        compress = should_compress(
            len(raw),
            opened.format,
            opened.width,
            opened.height,
            max_image_mb,
            max_width,
            grayscale,
            opened.mode,
        )
    if not compress:
        return keep_original(raw)
    return compress_image(raw, max_width=max_width, quality=quality, grayscale=grayscale)
