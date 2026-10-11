# Сборка установщика «Фабрика Lab-100» (Inno Setup 6).
#
# 1) собирает dist\factory.exe (если его ещё нет) — scripts\build_exe.ps1;
# 2) собирает установщик dist\factory-setup.exe по scripts\factory.iss.
#
# Если Inno Setup (ISCC.exe) не найден — скрипт печатает, как его поставить,
# и завершается кодом 2, ничего не ломая.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Find-Iscc {
    $candidates = @()
    foreach ($base in @(${env:ProgramFiles}, ${env:ProgramFiles(x86)})) {
        if ($base) { $candidates += (Join-Path $base "Inno Setup 6\ISCC.exe") }
    }
    # winget/шоколадные установки и настраиваемое место — ищем по реестру
    foreach ($hive in @("HKLM:", "HKCU:")) {
        foreach ($sub in @(
            "Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
            "Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
        )) {
            try {
                $install = (Get-ItemProperty -Path "$hive\$sub" -ErrorAction Stop).InstallLocation
                if ($install) { $candidates += (Join-Path $install "ISCC.exe") }
            } catch { }
        }
    }
    # последний шанс — из PATH
    $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Host "Inno Setup (ISCC.exe) не найден."
    Write-Host "Установите Inno Setup 6:  winget install --id JRSoftware.InnoSetup"
    Write-Host "или скачайте с https://jrsoftware.org/isdl.php и запустите скрипт снова."
    Write-Host "Скрипт установщика уже лежит в scripts\factory.iss — можно собрать вручную:"
    Write-Host "  ISCC.exe /DAppVersion=<версия> scripts\factory.iss"
    exit 2
}

if (-not (Test-Path "dist\factory.exe")) {
    Write-Host "Собираю dist\factory.exe..."
    & (Join-Path $PSScriptRoot "build_exe.ps1")
}

$version = (Get-Content "project.json" -Raw -Encoding UTF8 | ConvertFrom-Json).version
Write-Host "Inno Setup: $iscc"
Write-Host "Версия:     $version"

& $iscc "/DAppVersion=$version" (Join-Path "scripts" "factory.iss")

if (Test-Path "dist\factory-setup.exe") {
    $size = (Get-Item "dist\factory-setup.exe").Length
    Write-Host ("OK: dist\factory-setup.exe ({0:N0} байт)" -f $size)
} else {
    Write-Error "Установщик не собран"
    exit 1
}
