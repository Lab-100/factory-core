<#
    Самопочинка фабрики Lab-100: прогон тестов и возврат рабочей копии к зелени.

    Использование:
      .\selfheal.ps1                        — диагностика (тесты и состояние git)
      .\selfheal.ps1 -Fix                   — починить: сломанные правки уходят в git stash
      .\selfheal.ps1 -Fix -Commit -Push     — плюс коммит и отправка зелёного состояния
      .\selfheal.ps1 -Install               — задача планировщика: запуск каждые 15 минут
      .\selfheal.ps1 -Install -Interval 30  — …каждые 30 минут (с -Fix по умолчанию)
      .\selfheal.ps1 -Uninstall             — снять задачу планировщика

    Задача запускает этот же скрипт с -Fix -Quiet, поэтому рабочая копия сама
    возвращается к зелёному состоянию — без участия человека.
#>
[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$Commit,
    [switch]$Push,
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
    # Шим фабрики: factory.exe рядом, dist\factory.exe, иначе `python -m factory`.
    $exe = Join-Path $here "factory.exe"
    if (-not (Test-Path $exe)) { $exe = Join-Path $root "dist\factory.exe" }
    if (Test-Path $exe) { return @($exe) }
    $py = "python"
    if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
    return @($py, "-m", "factory")
}

function Invoke-Heal {
    $base = Resolve-Factory
    $exe = $base[0]
    $prefix = @()
    if ($base.Count -gt 1) { $prefix = $base[1..($base.Count - 1)] }
    $cliArgs = @("heal", "--timeout", "$Timeout")
    if ($Fix) { $cliArgs += "--fix" }
    if ($Commit) { $cliArgs += "--commit" }
    if ($Push) { $cliArgs += "--push" }
    if ($Json) { $cliArgs += "--json" }

    Push-Location $root
    try {
        $output = & $exe @prefix @cliArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    $dir = Split-Path -Parent $Log
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $stamp = (Get-Date).ToUniversalTime().ToString("s") + "Z"
    Add-Content -Path $Log -Value ("[{0}] rc={1} {2}" -f $stamp, $code, ($output -join " | ")) -Encoding UTF8
    if (-not $Quiet) { $output | ForEach-Object { Write-Output $_ } }
    return $code
}

function Install-Task {
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Fix -Quiet -Timeout $Timeout"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $Interval) `
        -RepetitionDuration ([TimeSpan]::MaxValue)
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
exit (Invoke-Heal)
