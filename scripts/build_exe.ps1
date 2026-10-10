# Сборка Windows-версии factory-core в один exe (PyInstaller).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "Устанавливаю PyInstaller..."
py -m pip install --upgrade pyinstaller

Write-Host "Собираю factory.exe (onefile)..."
py -m PyInstaller --noconfirm --clean --onefile --name factory `
  --add-data "config;config" `
  --add-data "schemas;schemas" `
  --add-data "examples;examples" `
  run.py

if (-not (Test-Path "dist\factory.exe")) { Write-Error "Сборка не удалась"; exit 1 }
Write-Host "OK: dist\factory.exe"

# Проверка: версия сборки
& "dist\factory.exe" status 2>&1 | Select-Object -First 4