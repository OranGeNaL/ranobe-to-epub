"""Точка входа для `python -m ranobelib_epub` и для сборки Nuitka.

Nuitka запускает пакет через `--python-flag=-m`, поэтому `__name__` модуля `cli.main`
при такой сборке равен `ranobelib_epub.cli.main`, а не `__main__`, и защита
`if __name__ == "__main__":` в нём не срабатывает. Вызов `run()` здесь явный.
"""

from __future__ import annotations

from .cli.main import run

run()