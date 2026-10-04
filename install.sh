#!/usr/bin/env bash
#
# Установка ranobelib-epub для macOS и Linux.
#
# Скрипт получает последний релиз из GitHub Releases, скачивает бинарный файл
# под платформу/архитектуру пользователя, проверяет его контрольную сумму
# SHA-256 по checksums.txt и устанавливает в ~/.local/bin (или в
# $RANOBELIB_EPUB_INSTALL_DIR, если переменная задана).
#
# Запуск:
#   curl -fsSL https://raw.githubusercontent.com/OranGeNaL/ranobe-to-epub/main/install.sh | sh
#
# Переменные окружения:
#   RANOBELIB_EPUB_INSTALL_DIR  — директория установки (по умолчанию ~/.local/bin)
#   RANOBELIB_EPUB_API_URL      — URL GitHub API последнего релиза (для тестов)
#   RANOBELIB_EPUB_DOWNLOAD_URL — базовый URL скачивания артефактов (для тестов)

set -euo pipefail

REPO="OranGeNaL/ranobe-to-epub"
BIN_NAME="ranobelib-epub"

API_URL="${RANOBELIB_EPUB_API_URL:-https://api.github.com/repos/${REPO}/releases/latest}"
DOWNLOAD_URL="${RANOBELIB_EPUB_DOWNLOAD_URL:-https://github.com/${REPO}/releases/download}"

log() {
    printf '%s\n' "$*"
}

fail() {
    printf 'Ошибка: %s\n' "$*" >&2
    exit 1
}

detect_platform() {
    local os arch
    os="$(uname -s)"
    arch="$(uname -m)"
    case "$os" in
        Darwin) os="macos" ;;
        Linux) os="linux" ;;
        *) fail "Неподдерживаемая операционная система: $os" ;;
    esac
    case "$arch" in
        x86_64 | amd64) arch="x86_64" ;;
        arm64 | aarch64) arch="arm64" ;;
        *) fail "Неподдерживаемая архитектура: $arch" ;;
    esac
    if [ "$os" = "macos" ] && [ "$arch" = "x86_64" ]; then
        fail "Сборок под macOS Intel нет. Используйте установку через uv: uv run ranobelib-epub"
    fi
    if [ "$os" = "linux" ] && [ "$arch" = "arm64" ]; then
        fail "Сборок под Linux arm64 нет. Используйте установку через uv: uv run ranobelib-epub"
    fi
    printf '%s-%s\n' "$os" "$arch"
}

get_latest_tag() {
    local json tag
    json="$(curl -fsSL --max-time 30 "$API_URL")" || fail "Не удалось получить последний релиз с GitHub (нет сети или сервис недоступен)"
    tag="$(printf '%s' "$json" | grep -oE '"tag_name"[^,]*' | head -n 1 | sed -E 's/.*"([^"]+)".*/\1/')"
    if [ -z "$tag" ]; then
        fail "Не удалось определить версию последнего релиза"
    fi
    printf '%s\n' "$tag"
}

verify_sha256() {
    local dir="$1" name="$2" filtered="${1}/checksums-${2}.txt" line
    line="$(grep -E " ${name}$" "${dir}/checksums.txt" || true)"
    if [ -z "$line" ]; then
        fail "В checksums.txt нет контрольной суммы для $name"
    fi
    printf '%s\n' "$line" > "$filtered"
    if command -v sha256sum >/dev/null 2>&1; then
        (cd "$dir" && sha256sum -c "$(basename "$filtered")") >/dev/null
    else
        (cd "$dir" && shasum -a 256 -c "$(basename "$filtered")") >/dev/null
    fi
}

path_contains() {
    local dir="$1"
    case ":$PATH:" in
        *":$dir:"*) return 0 ;;
    esac
    return 1
}

tmp_dir=""

main() {
    local platform asset tag install_dir target

    log "Определение платформы..."
    platform="$(detect_platform)"
    asset="ranobelib-epub-${platform}"
    log "Платформа: $platform"

    log "Получение последней версии..."
    tag="$(get_latest_tag)"
    log "Последняя версия: $tag"

    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT

    log "Скачивание $asset..."
    curl -fsSL --max-time 300 "${DOWNLOAD_URL}/${tag}/${asset}" -o "${tmp_dir}/${asset}" \
        || fail "Не удалось скачать $asset"
    curl -fsSL --max-time 60 "${DOWNLOAD_URL}/${tag}/checksums.txt" -o "${tmp_dir}/checksums.txt" \
        || fail "Не удалось скачать checksums.txt"

    log "Проверка контрольной суммы..."
    verify_sha256 "$tmp_dir" "$asset" || fail "Контрольная сумма не совпадает; файл не установлен"

    install_dir="${RANOBELIB_EPUB_INSTALL_DIR:-${HOME}/.local/bin}"
    mkdir -p "$install_dir"
    target="${install_dir}/${BIN_NAME}"
    install -m 0755 "${tmp_dir}/${asset}" "$target"

    log "Установлено: $target"
    if path_contains "$install_dir"; then
        log "Запуск: ranobelib-epub --help"
    else
        log "Директория $install_dir не в PATH. Добавьте её:"
        log "  export PATH=\"$install_dir:\$PATH\""
        log "Запуск: ranobelib-epub --help"
    fi
}

main "$@"