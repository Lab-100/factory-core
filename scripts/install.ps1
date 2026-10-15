# Установка Фабрики Lab-100 для текущего пользователя Windows 10/11.
#
# Дистрибутив самодостаточен: распакуйте factory-core-win.zip в любую папку
# и запустите install.ps1 (он может лежать и в корне архива, и в scripts\).
# Все инструменты фабрики едут вместе с ним: каталог scripts\ и его копия
# tools.zip — если каталога рядом нет, он распакуется при установке.
# Python на целевой машине не нужен: работает собранный factory.exe.
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$install = Join-Path $env:LOCALAPPDATA "Programs\FactoryLab100"

function Find-Root([string] $start) {
    # Корень дистрибутива: где лежит factory.exe (или tools.zip), поднимаясь вверх.
    $dir = $start
    for ($i = 0; $i -lt 3; $i++) {
        if ((Test-Path (Join-Path $dir "factory.exe")) -or
            (Test-Path (Join-Path $dir "tools.zip"))) { return $dir }
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return $start
}

$src = Find-Root $here

# Инструменты: каталог scripts\ или архив tools.zip (распаковываем при установке)
$tools = Join-Path $src "scripts"
$toolsZip = Join-Path $src "tools.zip"
if ((-not (Test-Path $tools)) -and (Test-Path $toolsZip)) {
    Write-Host "Распаковываю инструменты: tools.zip -> scripts"
    Expand-Archive -Path $toolsZip -DestinationPath $tools -Force
}

if (-not (Test-Path (Join-Path $src "factory.exe"))) {
    throw "Не найден factory.exe рядом с install.ps1 ($src). Распакуйте архив целиком."
}

Write-Host "Устанавливаю в: $install"
New-Item -ItemType Directory -Force -Path $install | Out-Null
Copy-Item (Join-Path $src "factory.exe") $install -Force

foreach ($dir in @("config", "schemas", "examples", "docs")) {
    if (-not (Test-Path (Join-Path $src $dir))) { continue }
    New-Item -ItemType Directory -Force -Path (Join-Path $install $dir) | Out-Null
    Copy-Item (Join-Path $src $dir) (Join-Path $install $dir) -Recurse -Force
}
foreach ($file in @("README.md", "LICENSE", "project.json", "factory.ps1")) {
    Copy-Item (Join-Path $src $file) $install -Force -ErrorAction SilentlyContinue
}
# Инструменты (скрипты установки/сборки, шим, установщик) — в подкаталог scripts
if (Test-Path $tools) {
    New-Item -ItemType Directory -Force -Path (Join-Path $install "scripts") | Out-Null
    Copy-Item (Join-Path $tools "*") (Join-Path $install "scripts") -Recurse -Force
}

# PATH (только для пользователя)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$install*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$install", "User")
    Write-Host "Добавлено в PATH пользователя."
}

# Ярлыки в меню «Пуск»
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Фабрика Lab-100"
New-Item -ItemType Directory -Force -Path $menu | Out-Null
$shortcuts = @(
    @{ Name = "Фабрика Lab-100 (окно)"; Arguments = "ui --mode desktop --open" },
    @{ Name = "Дашборд в браузере"; Arguments = "ui --mode web --open" },
    @{ Name = "Состояние фабрики"; Arguments = "status" }
)
foreach ($item in $shortcuts) {
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut(
        (Join-Path $menu ($item.Name + ".lnk")))
    $shortcut.TargetPath = Join-Path $install "factory.exe"
    $shortcut.Arguments = $item.Arguments
    $shortcut.WorkingDirectory = $install
    $shortcut.Save()
}

Write-Host "Установлено. Проверка:"
& (Join-Path $install "factory.exe") status 2>&1 | Select-Object -First 4
Write-Host ""
Write-Host "Запуск: factory (из любого терминала), либо ярлыки в меню «Пуск»."
Write-Host "Для установки как Windows-приложения (Программы и компоненты) используйте"
Write-Host "installer из релиза: factory-setup.exe."
