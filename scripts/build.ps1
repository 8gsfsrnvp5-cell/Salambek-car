<#
  Воспроизводимая сборка AutoServiceAI-Setup.exe на Windows:
  исходный код → чистое окружение → тесты → PyInstaller → Inno Setup.

  Запуск из корня репозитория:
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1
  Параметры:
    -SkipTests   не запускать автотесты (не рекомендуется)
    -Iscc PATH   путь к ISCC.exe, если Inno Setup установлен не в стандартную папку
#>
param(
    [switch]$SkipTests,
    [string]$Iscc = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = (Select-String -Path "src\autoservice\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "AutoService AI $Version — сборка" -ForegroundColor Cyan

# 1. Чистое окружение сборки только с зависимостями программы
$Venv = Join-Path $Root ".venv-build"
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv $Venv } else { python -m venv $Venv }
}
$Py = "$Venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip --quiet
# SQLAlchemy ставится без C-ускорителей: неподписанные .pyd блокируются Smart App Control.
$env:DISABLE_SQLALCHEMY_CEXT = "1"
$env:PYTHONUTF8 = "1"
& $Py -m pip install --quiet --no-binary SQLAlchemy -r requirements.txt
& $Py -m pip install --quiet pytest==9.2.0 pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.9
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }

# 2. Тесты
if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Автотесты не пройдены — сборка остановлена" }
    Remove-Item Env:\QT_QPA_PLATFORM
}

# 3. PyInstaller
& $Py -m PyInstaller installer\AutoServiceAI.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с ошибкой" }
Get-ChildItem -Path dist\AutoServiceAI -Recurse -Filter "*_cy*.pyd" | ForEach-Object { throw "В сборку попал C-модуль SQLAlchemy: $_" }

# 4. Inno Setup
if (-not $Iscc) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw "Не найден Inno Setup 6 (ISCC.exe). Установите: winget install JRSoftware.InnoSetup" }
& $Iscc "/DAppVersion=$Version" "installer\AutoServiceAI.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup завершился с ошибкой" }

$Setup = Join-Path $Root "installer\Output\AutoServiceAI-Setup.exe"
$Hash = (Get-FileHash $Setup -Algorithm SHA256).Hash
"$Hash  AutoServiceAI-Setup.exe" | Out-File -Encoding ascii "$Setup.sha256"
Write-Host "Готово: $Setup" -ForegroundColor Green
Write-Host "SHA-256: $Hash"
