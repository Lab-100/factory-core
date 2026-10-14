<#
    Самопочинка фабрики Lab-100: прогон тестов и возврат рабочей копии к зелени.

    Использование:
      .\selfheal.ps1                        — диагностика (тесты и состояние git)
      .\selfheal.ps1 -Fix                   — починить: сломанные правки уходят в git stash
      .\selfheal.ps1 -Publish               — коммит/пуш завершённой работы (чужую не трогает)
      .\selfheal.ps1 -Publish -Wait 600     — …с ожиданием завершения чужой работы до 10 минут
      .\selfheal.ps1 -Install               — задача планировщика каждые 15 минут
                                              (по умолчанию: -Fix -Publish -Wait 600)
      .\selfheal.ps1 -Uninstall             — снять задачу планировщика

    Задача запускает этот же скрипт с -Quiet, поэтому рабочая копия сама
    возвращается к зелёному состоянию, а завершённая работа публикуется —
    без участия человека и без помех тому, кто ещё пишет код.
#>
[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$Commit,
    [switch]$Push,
    [switch]$Publish,
    [double]$Wait = 0,
    [switch]$Json,
    [switch]$Quiet,
    [switch]$Install,
    [switch]$Uninstall,
    [int]$Interval = 15,
    [double]$Timeout = 900,
    [string]$TaskName = "FactoryLab100-SelfHeal",
    [string]$Log = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
if (-not $Log) { $Log = Join-Path $root "state\selfheal.tasks.log" }

function Resolve-Factory {
    # Для самопочинки репозитория приоритет — исходники (`python -m factory`):
    # собранный factory.exe может быть старой сборкой без новых команд.
    foreach ($name in @("py", "python", "python3")) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) { return @($found.Source, "-m", "factory") }
    }
    $exe = Join-Path $here "factory.exe"
    if (-not (Test-Path $exe)) { $exe = Join-Path $root "dist\factory.exe" }
    if (Test-Path $exe) { return @($exe) }
    return @("python", "-m", "factory")
}

function Invoke-Heal {
    $base = @(Resolve-Factory)
    $exe = $base[0]
    $prefix = @()
    if ($base.Count -gt 1) { $prefix = $base[1..($base.Count - 1)] }
    $cliArgs = @("heal", "--timeout", "$Timeout")
    if ($Fix) { $cliArgs += "--fix" }
    if ($Publish -or $Commit) { $cliArgs += "--commit" }
    if ($Publish -or $Push) { $cliArgs += "--push" }
    if ($Wait -gt 0) { $cliArgs += @("--wait", "$Wait") }
    if ($Json) { $cliArgs += "--json" }

    # Внешний вывод фабрики — UTF-8; иначе под планировщиком получаются кракозябры.
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
    $env:PYTHONIOENCODING = "utf-8"

    Push-Location $root
    try {
        $output = & $exe @prefix @cliArgs 2>&1
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
    } catch {
        $output = @("ошибка запуска фабрики: " + $_.Exception.Message)
        $code = 1
    } finally {
        Pop-Location
    }

    $dir = Split-Path -Parent $Log
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $stamp = (Get-Date).ToUniversalTime().ToString("s") + "Z"
    Add-Content -Path $Log -Value ("[{0}] rc={1} {2}" -f $stamp, $code, ($output -join " | ")) -Encoding UTF8
    if (-not $Quiet) { $output | ForEach-Object { Write-Host $_ } }
    return $code
}

function Install-Task {
    # В автономном режиме публикация включена: коммит/пуш выполняются, только
    # когда чужой работы нет; иначе задача ждёт её завершения (по умолчанию 10 минут).
    $idleWait = if ($Wait -gt 0) { $Wait } else { 600 }
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Fix -Publish -Wait $idleWait -Quiet -Timeout $Timeout"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $Interval) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $trigger.Repetition = $repeat.Repetition
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Description "Самопочинка фабрики Lab-100: тесты и возврат к зелёному состоянию" -Force | Out-Null
    Write-Output "Задача планировщика зарегистрирована: $TaskName (каждые $Interval мин)"
    if (Test-Path (Join-Path $root ".githooks")) {
        Push-Location $root
        try { git config core.hooksPath .githooks } finally { Pop-Location }
        Write-Output "Git-хуки включены: core.hooksPath = .githooks (push без зелёных тестов отклоняется)"
    }
}

function Uninstall-Task {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Задача планировщика снята: $TaskName"
    } else {
        Write-Output "Задача не найдена: $TaskName"
    }
}

if ($Install) { Install-Task; return }
if ($Uninstall) { Uninstall-Task; return }
$code = Invoke-Heal
exit $code
