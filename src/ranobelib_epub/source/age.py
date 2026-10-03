"""Подтверждение возрастного ограничения `18+` (решение 11, задачи 6.1-6.5).

Что установлено на ranobelib.me: метка `18+` — это `ageRestriction.id = 4`, а
`18+ (RX)` — `id = 5`, причём RX существует только на площадках Hentai/Yaoi/Manga
(`site_ids: [4, 2, 5]`) и на RanobeLib (`Site-Id: 3`) отсутствует. Отметка «Мне есть
18+» хранится в Pinia-сторе `tipMessages` (localStorage) и на сервер не отправляется;
`registrationOnly = notAuthorized && (id === 5 || site ∈ {Hentai, Yaoi, Manga})` для
site 3 всегда `false`.

Следствие: для `id <= 4` блокировки нет, и подтверждение сводится к фиксации
декларации возраста, которую пользователь и так заявил своим запросом. Дополнительных
запросов, подмены заголовков и повторных попыток не требуется — их не существует.
Авторизация и RX — настоящий контроль доступа, они не обходятся.
"""

from __future__ import annotations

from ..models import Book, Report

#: Уровни ограничения из `/constants?fields[]=ageRestriction`.
AGE_RESTRICTIONS: dict[int, str] = {
    1: "Без ограничений",
    2: "12+",
    3: "16+",
    4: "18+",
    5: "18+ (RX)",
}

#: Всё, что не старше обычной метки `18+`, подтверждается автоматически.
AUTO_CONFIRM_LIMIT = 4


def restriction_label(age_restriction_id: int | None) -> str:
    """Метка ограничения; неизвестное значение трактуется как отсутствие ограничения.

    Откат в «без ограничений» выбран потому, что отсутствие или неизвестная метка не
    должно приводить к отказу там, где сервер контент отдаёт.
    """
    if age_restriction_id is None:
        return AGE_RESTRICTIONS[1]
    return AGE_RESTRICTIONS.get(age_restriction_id, AGE_RESTRICTIONS[1])


def requires_confirmation(book: Book) -> bool:
    """Требуется ли подтверждение возраста: метка `18+` и ниже."""
    level = book.age_restriction_id
    return level is not None and 1 < level <= AUTO_CONFIRM_LIMIT


def is_rx(book: Book) -> bool:
    """Метка `18+ (RX)` — единственное ограничение, которое не обходится."""
    level = book.age_restriction_id
    return level is not None and level > AUTO_CONFIRM_LIMIT


def confirm_age(book: Book, report: Report | None = None) -> str:
    """Подтверждает возрастное ограничение и возвращает описание для отчёта.

    Ничего не отправляет и никуда не ходит: блокировки на сервере нет, а подтверждение —
    фиксация декларации пользователя. Факт обязательно отражается в отчёте, иначе
    автоматическое подтверждение выглядело бы как молчаливый обход ограничения.
    """
    label = restriction_label(book.age_restriction_id)
    message = (
        f"возрастное ограничение «{label}» ({book.age_restriction_id}) подтверждено автоматически"
    )

    if report is not None:
        report.age_restriction_id = book.age_restriction_id
        report.age_restriction_label = label
        report.age_confirmed_chapters += 1
        report.notes.append(message)

    return message
