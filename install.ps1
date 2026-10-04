<#
    Установка ranobelib-epub для Windows.

    Скрипт получает последний релиз из GitHub Releases, скачивает бинарный
    файл Windows x86_64, проверяет его контрольную сумму SHA-256 по
    checksums.txt и устанавливает в %LOCALAPPDATA%\Programs\ranobelib-epub
    (или в $env:RANOBELIB_EPUB_INSTALL_DIR, если переменная задана).

    Запуск:
      irm https://raw.githubusercontent.com/OranGeNaL/ranobe-to-epub/main/install.ps1 | iex

    Переменные окружения:
      RANOBELIB_EPUB_INSTALL_DIR  — директория установки (по умолчанию %LOCALAPPDATA%\Programs\ranobelib-epub)
      RANOBELIB_EPUB_API_URL      — URL GitHub API последнего релиза (для тестов)
      RANOBELIB_EPUB_DOWNLOAD_URL — базовый URL скачивания артефактов (для тестов)
#>

$ErrorActionPreference = 'Stop'

$Repo = 'OranGeNaL/ranobe-to-epub'
$BinName = 'ranobelib-epub.exe'
$Asset = 'ranobelib-epub-windows-amd64.exe'

$ApiUrl = if ($env:RANOBELIB_EPUB_API_URL) { $env:RANOBELIB_EPUB_API_URL } else { "https://api.github.com/repos/$Repo/releases/latest" }
$DownloadUrl = if ($env:RANOBELIB_EPUB_DOWNLOAD_URL) { $env:RANOBELIB_EPUB_DOWNLOAD_URL } else { "https://github.com/$Repo/releases/download" }

function Write-Step {
    param([string]$Message)
    Write-Host $Message
}

function Write-Fail {
    param([string]$Message)
    Write-Host "Ошибка: $Message" -ForegroundColor Red
    exit 1
}

function Detect-Arch {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($arch -ne 'AMD64') {
        Write-Fail "Неподдерживаемая архитектура: $arch. Доступна сборка только для Windows x86_64 (AMD64). Используйте установку через uv: uv run ranobelib-epub"
    }
    return 'windows-amd64'
}

function Get-LatestTag {
    try {
        $release = Invoke-RestMethod -Uri $ApiUrl -TimeoutSec 30
    } catch {
        Write-Fail "Не удалось получить последний релиз с GitHub (нет сети или сервис недоступен)"
    }
    if (-not $release.tag_name) {
        Write-Fail "Не удалось определить версию последнего релиза"
    }
    return $release.tag_name
}

function Verify-Sha256 {
    param(
        [string]$Dir,
        [string]$Name
    )
    $checksums = Join-Path $Dir 'checksums.txt'
    $line = Select-String -Path $checksums -Pattern " $([regex]::Escape($Name))$"
    if (-not $line) {
        Write-Fail "В checksums.txt нет контрольной суммы для $Name"
    }
    $expected = ($line.Line -split '\s+')[0]
    $actual = (Get-FileHash -Algorithm SHA256 -Path (Join-Path $Dir $Name)).Hash
    if ($actual.ToLower() -ne $expected.ToLower()) {
        Write-Fail "Контрольная сумма не совпадает; файл не установлен"
    }
}

function Test-InPath {
    param([string]$Dir)
    return ($env:Path -split ';') -contains $Dir
}

$platform = Detect-Arch
Write-Step "Платформа: $platform"
Write-Step 'Получение последней версии...'
$tag = Get-LatestTag
Write-Step "Последняя версия: $tag"

$tmpDir = Join-Path ([IO.Path]::GetTempPath()) ('ranobelib-epub-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

Write-Step "Скачивание $Asset..."
try {
    Invoke-WebRequest -Uri "$DownloadUrl/$tag/$Asset" -OutFile (Join-Path $tmpDir $Asset) -TimeoutSec 300
    Invoke-WebRequest -Uri "$DownloadUrl/$tag/checksums.txt" -OutFile (Join-Path $tmpDir 'checksums.txt') -TimeoutSec 60
} catch {
    Write-Fail "Не удалось скачать $Asset"
}

Write-Step 'Проверка контрольной суммы...'
Verify-Sha256 -Dir $tmpDir -Name $Asset

$installDir = if ($env:RANOBELIB_EPUB_INSTALL_DIR) { $env:RANOBELIB_EPUB_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'Programs\ranobelib-epub' }
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$target = Join-Path $installDir $BinName
Copy-Item -Path (Join-Path $tmpDir $Asset) -Destination $target -Force

Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Step "Установлено: $target"
if (Test-InPath -Dir $installDir) {
    Write-Step 'Запуск: ranobelib-epub --help'
} else {
    Write-Step "Директория $installDir не в PATH. Добавьте её в PATH и перезапустите терминал."
    Write-Step 'Запуск: ranobelib-epub --help'
}