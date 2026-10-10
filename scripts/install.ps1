# Установка factory-core для текущего пользователя Windows.
# Распакуйте factory-core-win.zip в любую папку и запустите install.ps1.
$ErrorActionPreference = "Stop"

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$install = Join-Path $env:LOCALAPPDATA "Programs\FactoryLab100"

Write-Host "Устанавливаю в: $install"

New-Item -ItemType Directory -Force -Path $install | Out-Null
Copy-Item (Join-Path $src "factory.exe") $install -Force

foreach ($dir in @("config", "schemas", "examples")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $install $dir) | Out-Null
    Copy-Item (Join-Path $src $dir) (Join-Path $install $dir) -Recurse -Force
}
Copy-Item (Join-Path $src "README.md") $install -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $src "LICENSE") $install -Force -ErrorAction SilentlyContinue

# PATH (только для пользователя)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$install*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$install", "User")
    Write-Host "Добавлено в PATH пользователя."
}

# Ярлыки в меню «Пуск»
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Фабрика Lab-100"
New-Item -ItemType Directory -Force -Path $menu | Out-Null
$ws = New-Object -ComObject WScript.Shell
$exe = Join-Path $install "factory.exe"
$link1 = $ws.CreateShortcut((Join-Path $menu "Дашборд.lnk"))
$link1.TargetPath = $exe; $link1.Arguments = "ui --mode web"; $link1.WorkingDirectory = $install; $link1.Save()
$link2 = $ws.CreateShortcut((Join-Path $menu "CLI.lnk"))
$link2.TargetPath = $exe; $link2.Arguments = "status"; $link2.WorkingDirectory = $install; $link2.Save()

Write-Host ""
Write-Host "Готово. Проверка:"
& $exe status
Write-Host ""
Write-Host "Запуск дашборда:  factory ui --mode web"
Write-Host "Или через меню «Пуск → Фабрика Lab-100 → Дашборд»."