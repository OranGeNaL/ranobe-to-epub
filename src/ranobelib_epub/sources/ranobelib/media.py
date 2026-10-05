"""Origin сайта и заголовки CDN для иллюстраций RanobeLib.

Файлы иллюстраций лежат на origin сайта, а не на хосте API, и отдаются только с
`Referer` и браузерным `User-Agent`: без них CDN отвечает 403, а путь на хосте API
отдаёт 404. Эти данные принадлежат сайту, поэтому живут в модуле, а не в общем
слое изображений.
"""

from __future__ import annotations

#: Origin сайта: и API-заголовки, и файлы иллюстраций привязаны к нему.
SITE_ORIGIN = "https://ranobelib.me"

#: Заголовки, без которых CDN отдаёт 403 на `/uploads/`.
IMAGE_HEADERS = {
    "Referer": f"{SITE_ORIGIN}/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
