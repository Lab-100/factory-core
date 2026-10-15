# Скрытый запуск интерфейса фабрики — без окна консоли.
#
# Нужен, чтобы ярлыки «Фабрика Lab-100 (окно)», «Дашборд в браузере» и автозапуск
# не оставляли на экране пустое окно консоли с иконкой фабрики. Сам интерфейс
# (окно или браузер) при этом работает как обычно; повторный запуск гасит
# одиночный экземпляр и не плодит окна.
#
# Использование:
#   powershell -NoProfile -WindowStyle Hidden -File factory-gui.ps1 web -Open
param(
    [ValidateSet("web", "desktop", "tray")][string]$Mode = "web",
    [switch]$Open
)

$app = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $app "factory.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    $exe = Join-Path $app "dist\factory.exe"
}
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Error "factory.exe не найден рядом со скриптом: $app"
    exit 1
}

$argList = @("ui", "--mode", $Mode)
if ($Open) { $argList += "--open" }

Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $app -WindowStyle Hidden
exit 0