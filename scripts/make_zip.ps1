# Упаковка Windows-дистрибутива dist\factory-core-win.zip.
#
# В архив входит всё для установки на любой машине Windows 10/11:
#   factory.exe, config\, schemas\, examples\, docs\, README.md, LICENSE,
#   project.json, все инструменты (scripts\ и его копия tools.zip),
#   обёртки install.ps1 / uninstall.ps1 / factory.ps1 и, если собран,
#   установщик factory-setup.exe.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if (-not (Test-Path "dist\factory.exe")) { Write-Error "Сначала запустите scripts\build_exe.ps1"; exit 1 }

$pkg = "dist\package"
Remove-Item -Recurse -Force $pkg -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $pkg | Out-Null

Copy-Item "dist\factory.exe" $pkg
foreach ($dir in @("config", "schemas", "examples", "docs")) {
    Copy-Item -Recurse $dir (Join-Path $pkg $dir)
}
foreach ($file in @("README.md", "LICENSE", "project.json")) {
    if (Test-Path $file) { Copy-Item $file $pkg }
}

# Все инструменты: каталог scripts\ целиком и он же одним архивом tools.zip
if (Test-Path "scripts") {
    Copy-Item -Recurse "scripts" (Join-Path $pkg "scripts")
    $toolsZip = Join-Path $pkg "tools.zip"
    Remove-Item -Force $toolsZip -ErrorAction SilentlyContinue
    Compress-Archive -Path "scripts\*" -DestinationPath $toolsZip
}
foreach ($file in @("install.ps1", "uninstall.ps1", "factory.ps1")) {
    $src = Join-Path "scripts" $file
    if (Test-Path $src) { Copy-Item $src $pkg }
}
if (Test-Path "dist\factory-setup.exe") { Copy-Item "dist\factory-setup.exe" $pkg }

$zip = "dist\factory-core-win.zip"
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$pkg\*" -DestinationPath $zip
Write-Host "OK: $zip ($([Math]::Round((Get-Item $zip).Length / 1MB, 1)) МБ)"
