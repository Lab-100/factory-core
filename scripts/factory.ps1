# Обёртка-шим фабрики Lab-100 (§8.3): «Lab100 → инструмент → каталог» одной командой.
#
# Использование:
#   factory.ps1 run <task.json>            — выполнить заказ-наряд (пишет в каталог)
#   factory.ps1 catalog                    — показать каталог артефактов
#   factory.ps1 doctor                     — проверить адаптеры и интеграции
#   factory.ps1 <любая команда> [аргументы] — проброс в CLI фабрики
#
# Шим переносим: он ищет рядом factory.exe (собранная версия) либо запускает
# `python -m factory` из исходников. Машинные пути не прописаны.
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $here "factory.exe"
if (-not (Test-Path $exe)) { $exe = Join-Path (Split-Path -Parent $here) "dist\factory.exe" }

if (Test-Path $exe) {
    & $exe @args
} else {
    $py = "python"
    if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
    & $py -m factory @args
}
exit $LASTEXITCODE
