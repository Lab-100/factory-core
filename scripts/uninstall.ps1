# Удаление factory-core (пользовательская установка).
$ErrorActionPreference = "Continue"

$install = Join-Path $env:LOCALAPPDATA "Programs\FactoryLab100"
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Фабрика Lab-100"

# удаляем ярлыки
Remove-Item -Recurse -Force $menu -ErrorAction SilentlyContinue

# убираем из PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -like "*$install*") {
    $userPath = ($userPath -split ";" | Where-Object { $_ -and $_ -ne $install }) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $userPath, "User")
}

# удаляем программу (рабочие данные в %LOCALAPPDATA%\FactoryLab100 сохраняются, если указать параметр -DeleteData)
$confirm = "y"
if ($args -contains "-DeleteData") {
    $confirm = Read-Host "Удалить и рабочие данные (газеты, каталог, журналы)? [y/N]"
}
if ($confirm -match "^y") {
    Remove-Item -Recurse -Force $install -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA "FactoryLab100") -ErrorAction SilentlyContinue
}
Write-Host "Готово."