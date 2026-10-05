## Context

См. `proposal.md` — Why. Текущий слой `src/ranobelib_epub/source/` уже почти изолирован: он отдаёт доменные модели из `models.py`, а потребители (`pipeline/downloader.py`, `cli/main.py`, `tui/app.py`) не парсят JSON. Мешают три вещи: (1) потребители импортируют конкретные `RanobeLibClient`/`RanobeLibSource` и обращаются к `source.client`; (2) site-specific константы (`DEFAULT_API_BASE`, `DEFAULT_SITE_ID`, `DEFAULT_SITE_ORIGIN`, `IMAGE_HEADERS`, `SITE_ORIGIN`) лежат в общих `models.py` и `images/pipeline.py`; (3) нет выбора источника по ссылке. Есть 19 тестовых файлов, часть из них импортирует `ranobelib_epub.source.*` напрямую.

## Goals / Non-Goals

**Goals:**
- Единый рантайм-контракт источника книги, достаточный для полной сборки EPUB без доступа к внутренностям модуля.
- Реестр источников и выбор по пользовательской ссылке.
- RanobeLib как первый модуль, полностью реализующий контракт и держащий site-specific данные у себя.
- Потребители (`pipeline`, `epub`, `tui`, `cli`) зависят только от контракта и моделей.

**Non-Goals:**
- Реализация второго сайта-источника (только архитектурная готовность).
- Изменение пользовательского поведения, формата EPUB, CLI-флагов и TUI.
- Плагины как внешние устанавливаемые пакеты (источники — модули внутри проекта).
- Изменение доменных моделей книги/главы за пределами переноса констант.

## Decisions

### 1. Пакет `sources/` с фреймворком и модулем `sources/ranobelib/`
Новая структура:
```
src/ranobelib_epub/sources/
  __init__.py      # публичный API: BookSource, SourceConfig, SourceError, create_source, resolve_source
  base.py          # протокол BookSource, SourceModule, SourceConfig, базовые ошибки
  registry.py      # SourceRegistry и реестр по умолчанию
  ranobelib/
    __init__.py    # регистрация модуля, реэкспорт RanobeLibSource
    client.py      # из source/client.py; константы сайта — локально
    api.py         # RanobeLibSource реализует BookSource
    parsing.py     # из source/parsing.py
    url.py         # из source/url.py; origin — локально
    age.py         # из source/age.py
    translations.py
    numbering.py
    media.py       # origin сайта и заголовки CDN для иллюстраций
```
Альтернатива — оставить `source/` и добавить протокол рядом: не даёт явной границы модуля и не выполняет требование выделения RanobeLib в отдельный модуль. Выбран отдельный подпакет.

### 2. Два уровня контракта: `SourceModule` (выбор и создание) и `BookSource` (рантайм)
`SourceModule` описывает источник до создания: `name`, `matches(url) -> bool`, `parse(url) -> ref` (чистый разбор без сети), `create(config) -> BookSource`. `BookSource` — рантайм-объект с операциями данных:
- `fetch_book(ref)`, `fetch_chapters(ref)`, `fetch_covers(ref)`, `fetch_chapter_content(ref, chapter, branch_id)`;
- `resource_url(url)` — абсолютный URL ресурса (для отчёта и превью);
- `fetch_resource(url)` — байты ресурса с нужными сайту заголовками;
- `chapter_unavailable_reason(chapter)` — статическая причина до запроса;
- `describe_failure(error, *, ref="")` — человекочитаемая причина сбоя (ref даёт эндпоинт);
- `is_authorization_error(error)`;
- `aclose()`.

`BookSource` — `typing.Protocol` (структурная типизация): модулю не нужно наследоваться от общего базового класса, что упрощает тестовые двойники. `RanobeLibSource(client)` сохраняет возможность внедрить поддельный клиент, как сейчас.

### 3. Реестр и выбор по ссылке
`SourceRegistry` хранит `SourceModule` и умеет `resolve(url)` (по `matches`) и `create(url, config)`. Реестр по умолчанию заполняется при импорте `sources`: `sources/ranobelib/__init__.py` вызывает `register(...)`. Неподдерживаемая ссылка даёт `UnsupportedSourceError` до создания сетевого клиента. Тесты могут подменять реестр, но инъекция готового `BookSource` в `run_build`/`list_covers_flow` через параметр `source=` сохраняется для существующих сценариев.

### 4. Бинарные ресурсы уходят за контракт источника
`RanobeLibSource.fetch_resource(url)` вызывает `client.get_bytes(url, headers=media.IMAGE_HEADERS)`, а `resource_url(url)` достраивает origin модуля. `pipeline/downloader.py` перестаёт импортировать `absolute_url`/`fetch_image`/`IMAGE_HEADERS` и вызывает только методы источника. `images/pipeline.py` теряет `SITE_ORIGIN`/`IMAGE_HEADERS` и остаётся про сжатие и разбор ключей изображений. Превью обложки в TUI получает байты через источник.

### 5. Классификация ошибок переезжает в источник
`describe_failure`, `chapter_unavailable_reason`, `is_authorization_error` становятся методами/функциями модуля. `pipeline` больше не импортирует `CHAPTER_PATH`, `NO_BRANCH_REASON`, `unavailable_reason`, `describe_failure` из `api`. Базовые типы (`SourceError`, `NotFoundError`, `UnsupportedSourceError`, `InvalidBookUrlError`) живут в `sources/base.py`, а `ApiError`/`BookNotFoundError`/`AuthorizationRequiredError`/`WafBlockedError` — в модуле RanobeLib и наследуются от базовых. `cli/main.py` ловит базовые типы, а не ranobelib-специфичные.

### 6. Доменные модели остаются в `models.py`, site-константы уезжают
`Book`, `Chapter`, `Cover`, `ChapterContent`, `Attachment`, `Report` и утилиты над ними остаются общими. `DEFAULT_API_BASE`, `DEFAULT_SITE_ID`, `DEFAULT_SITE_ORIGIN` удаляются из `models.py` и объявляются в модуле RanobeLib. `Attachment.absolute_url` сохраняется как помощник, но требует явный `origin` без site-значения по умолчанию; фактическое достраивание URL выполняет источник.

## Risks / Trade-offs

- **Массовое переименование `source` → `sources.ranobelib` ломает импорты в 19 тестах** → механическая замена импортов, полный прогон `pytest` и `ruff` как критерий готовности; поведенческие тесты не переписываются.
- **Циклические импорты вокруг `models`/`images`/`sorting`** → `sources/base.py` не импортирует конкретные модули; реестр заполняется в `sources/__init__.py` после определения базовых типов.
- **Расползание `describe_failure` с параметром `ref`** → метод принимает `ref` и сам строит эндпоинт; потребитель остаётся в терминах ссылки книги, а не пути API.
- **Поведенческая регрессия при переносе констант** → значения переносятся без изменений; существующие фикстуры и тесты подтверждают, что разбор и сборка не изменились.
- **Тестовые двойники, завязанные на `get_json`/`get_bytes` клиента** → сохраняем `RanobeLibClient` и его интерфейс в модуле, поэтому фиктивные `httpx.MockTransport` продолжают работать.

## Migration Plan

1. Создать `sources/base.py`, `sources/registry.py`, `sources/__init__.py` с протоколом и реестром.
2. Перенести `source/*` в `sources/ranobelib/*`, объявить site-константы и `media.py` локально, реализовать протокол в `RanobeLibSource`, зарегистрировать модуль.
3. Перевести `pipeline/downloader.py`, `cli/*`, `tui/app.py` на контракт и реестр.
4. Удалить `source/` и site-константы из `models.py`/`images/pipeline.py`.
5. Обновить импорты в тестах, прогнать `pytest` и `ruff`.
6. Откат — вернуть коммит целиком: изменение не трогает формат EPUB и внешние контракты.
