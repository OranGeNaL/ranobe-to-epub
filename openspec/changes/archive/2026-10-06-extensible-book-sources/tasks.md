## 1. Фреймворк источников

- [x] 1.1 Создать `src/ranobelib_epub/sources/base.py`: протокол `BookSource`, описатель `SourceModule`, `SourceConfig` (rate_limit, retries), `DEFAULT_RATE_LIMIT` и базовые ошибки `SourceError`, `NotFoundError`, `UnsupportedSourceError`, `InvalidBookUrlError`; проверить `python -c "import ranobelib_epub.sources.base"`.
- [x] 1.2 Создать `src/ranobelib_epub/sources/registry.py` с `SourceRegistry` (`register`, `modules`, `resolve(url)`, `create(url, config)`) и `sources/__init__.py` с публичным API `BookSource`, `SourceConfig`, `SourceError`, `resolve_source`, `create_source`; покрыть тестом выбора источника и ошибки `UnsupportedSourceError`.

## 2. Модуль RanobeLib

- [x] 2.1 Перенести сайт-специфичные файлы `source/*` (client, api, parsing, url, age) в `sources/ranobelib/*`, а доменные трансформации `numbering.py`/`translations.py` — в общие модули верхнего уровня (они работают только с моделями); проверить, что новые модули импортируются, а старые пути больше не используются.
- [x] 2.2 Объявить site-константы (`DEFAULT_API_BASE`, `DEFAULT_SITE_ID`, `DEFAULT_SITE_ORIGIN`) в модуле RanobeLib и создать `sources/ranobelib/media.py` с origin сайта и `IMAGE_HEADERS`; убрать эти значения из `models.py` и `images/pipeline.py`.
- [x] 2.3 Реализовать в `RanobeLibSource` контракт `BookSource`: `parse_url`, `matches`, `resource_url`, `fetch_resource` (через `client.get_bytes` с заголовками CDN), `chapter_unavailable_reason`, `describe_failure(error, *, ref="")`, `is_authorization_error`, `requires_age_confirmation`, `confirm_age`, `aclose`; проверить тестом на подделке транспорта, что иллюстрация скачивается с заголовками CDN.
- [x] 2.4 Зарегистрировать модуль RanobeLib в реестре по умолчанию в `sources/ranobelib/__init__.py` и подключить его в `sources/__init__.py`; проверить, что `resolve_source` на ссылке `ranobelib.me` возвращает модуль RanobeLib.
- [x] 2.5 Удалить старый пакет `src/ranobelib_epub/source/` и убедиться, что `ruff` не находит висячих ссылок.

## 3. Перевод потребителей на контракт

- [x] 3.1 Перевести `pipeline/downloader.py` на `BookSource`: убрать импорты `RanobeLibSource`, `CHAPTER_PATH`, `NO_BRANCH_REASON`, `unavailable_reason`, `describe_failure`, `absolute_url`, `fetch_image`, `source.client`; вызывать методы источника и переименовать `book_slug` в `book_ref`; проверить `tests/test_pipeline.py`.
- [x] 3.2 Перевести `cli/main.py` на реестр/контракт: убрать импорты `RanobeLibClient`/`RanobeLibSource` из `build_config`/`collect`/`run_build`/`list_covers_flow`, выбрать источник по ссылке и сохранить инъекцию через параметр `source=`; ловить базовые ошибки `sources.base`; проверить `tests/test_cli.py`.
- [x] 3.3 Убрать из `cli/options.py` импорты `source.client.DEFAULT_RATE_LIMIT` и `source.translations.DEFAULT_TRANSLATION`, взяв `DEFAULT_RATE_LIMIT` из `sources.base`; проверить `tests/test_cli.py`.
- [x] 3.4 Перевести `tui/app.py` на контракт: загрузка метаданных, превью обложки, сборка и работа с сетью идут через источник/реестр без импортов `source.*`; проверить `tests/test_tui.py`.
- [x] 3.5 Привести `models.py` и `images/pipeline.py` в соответствие: `Attachment.absolute_url` требует явный `origin`, из `models.py` удалены site-константы; проверить `tests/test_models.py` и `tests/test_images.py`.

## 4. Тесты

- [x] 4.1 Обновить импорты `ranobelib_epub.source.*` → `ranobelib_epub.sources.ranobelib.*` (а `numbering`/`translations` → общие модули) и тестовые двойники клиента в `tests/test_source.py`, `test_client.py`, `test_parsing.py`, `test_url.py`, `test_age.py`, `test_translations.py`, `test_numbering.py`, `test_contract.py`.
- [x] 4.2 Добавить тесты `book-source-api`: выбор источника по ссылке, ошибка на неподдерживаемой ссылке, изоляция потребителей (сборка работает с альтернативной реализацией-двойником контракта).

## 5. Проверка

- [x] 5.1 Запустить `ruff check` и `pytest`; убедиться, что поведение сборки, нумерации, перевода и возраста не изменилось.
- [x] 5.2 Запустить `openspec validate extensible-book-sources --strict` и убедиться, что изменение валидно.
