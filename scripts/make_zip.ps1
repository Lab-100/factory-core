# Упаковка Windows-версии в архив dist/factory-core-win.zip (вместе с install.ps1).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if (-not (Test-Path "dist\factory.exe")) { Write-Error "Сначала запустите scripts\build_exe.ps1"; exit 1 }

$pkg = "dist\package"
Remove-Item -Recurse -Force $pkg -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $pkg | Out-Null

Copy-Item "dist\factory.exe" $pkg
Copy-Item -Recurse "config" "$pkg\config"
Copy-Item -Recurse "schemas" "$pkg\schemas"
Copy-Item -Recurse "examples" "$pkg\examples"
Copy-Item -Recurse "docs" "$pkg\docs"
Copy-Item "README.md" $pkg
Copy-Item "LICENSE" $pkg
if (Test-Path "project.json") { Copy-Item "project.json" $pkg }
Copy-Item "scripts\install.ps1" "$pkg\install.ps1"
Copy-Item "scripts\uninstall.ps1" "$pkg\uninstall.ps1"
Copy-Item "scripts\factory.ps1" "$pkg\factory.ps1"

$zip = "dist\factory-core-win.zip"
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$pkg\*" -DestinationPath $zip
Write-Host "OK: $zip"