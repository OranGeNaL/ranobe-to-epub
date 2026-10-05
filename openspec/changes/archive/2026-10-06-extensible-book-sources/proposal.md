## Why

Сейчас проект умеет работать только с `ranobelib.me`: слой `source/` жёстко связан с потребителями (`pipeline`, `cli`, `tui`) через конкретные классы `RanobeLibClient`/`RanobeLibSource`, а site-specific константы и заголовки разбросаны по `models.py` и `images/pipeline.py`. Из-за этого добавление второго сайта потребовало бы правок во всех слоях сразу. Нужна расширяемая архитектура: единый контракт источника книги и вынесенный в отдельный модуль RanobeLib как первая его реализация.

## What Changes

- Вводится **API модулей взаимодействия с источниками книг** `book-source-api`: протокол `BookSource` (разбор ссылки, получение метаданных/глав/обложек/содержимого главы, скачивание ресурсов, классификация ошибок), реестр источников и выбор источника по пользовательской ссылке.
- Потребители (`pipeline`, `epub`, `tui`, `cli`) начинают зависеть только от протокола и доменных моделей; прямые импорты `RanobeLibClient`/`RanobeLibSource` и доступ к `source.client` убираются.
- Скачивание иллюстраций переводится на метод источника (`fetch_resource`/`attachment_url`), поэтому заголовки CDN и origin сайта перестают быть общими константами.
- **BREAKING (внутренний)**: слой `source/` переезжает в `sources/ranobelib/` и реализует протокол; импорты `ranobelib_epub.source.*` заменяются на `ranobelib_epub.sources.*`. Публичный CLI и поведение сборки не меняются.
- Site-specific данные (`DEFAULT_API_BASE`, `DEFAULT_SITE_ID`, `DEFAULT_SITE_ORIGIN`, заголовки `IMAGE_HEADERS`, политика возраста, выбор перевода) локализуются в модуле RanobeLib.

## Capabilities

### New Capabilities
- `book-source-api`: единый контракт источника книги, реестр и выбор источника по ссылке, изоляция потребителей от реализации, контракт ресурсов и классификации ошибок.

### Modified Capabilities
- `book-source-ranobelib`: RanobeLib SHALL реализовываться как зарегистрированный модуль, соответствующий `book-source-api`, и SHALL держать site-specific данные внутри себя; наблюдаемое поведение разбора ссылок, получения данных и сборки сохраняется.

## Impact

- Код: новый пакет `src/ranobelib_epub/sources/` (протокол, реестр, `sources/ranobelib/`); правки в `pipeline/downloader.py`, `cli/main.py`, `cli/options.py`, `tui/app.py`, `models.py`, `images/pipeline.py`.
- Тесты: обновляются импорты в `tests/test_source.py`, `tests/test_client.py`, `tests/test_parsing.py`, `tests/test_url.py`, `tests/test_age.py`, `tests/test_translations.py`, `tests/test_numbering.py`, `tests/test_cli.py`, `tests/test_contract.py`.
- Поведение CLI, формат EPUB и пользовательские сценарии не меняются.
