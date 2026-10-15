<#
Микромонитор запуска фабрики Lab-100.

Запускает фабрику (или другую команду), ловит её вывод, ошибки и код возврата,
пишет всё в журнал и показывает ошибки «столбиком» в окошке: дата, время,
источник (поток или файл) и текст ошибки. Нужен, когда программа сообщает об
ошибке и мгновенно закрывается — окно консоли мигает, и текст теряется.

Запуск: двойной клик по micromonitor.cmd либо

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\micromonitor.ps1

Параметры:
    -Exe <путь>        чем запускать (по умолчанию ищется factory.exe рядом,
                       затем установленный, затем `py -m factory`)
    -Arguments "..."   аргументы запуска (по умолчанию "ui --mode web":
                       тихий сервер дашборда без открытия новых вкладок)
    -LogDir <путь>     куда писать журнал (по умолчанию logs рядом с фабрикой)
    -NoAutoStart       не запускать цель сразу при открытии окна
#>
[CmdletBinding()]
param(
    [string]$Exe = "",
    [string]$Arguments = "ui --mode web",
    [string]$LogDir = "",
    [bool]$AutoStart = $true,
    [switch]$Background,
    [int]$RestartDelaySec = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Только один экземпляр: если окно монитора уже открыто — активируем его и выходим.
$existing = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowTitle -eq 'Микромонитор запуска — Фабрика Lab-100' -and $_.Id -ne $PID
})
if ($existing.Count -gt 0) {
    Add-Type -AssemblyName Microsoft.VisualBasic
    [Microsoft.VisualBasic.Interaction]::AppActivate($existing[0].Id)
    exit 0
}

# Фоновый режим (для планировщика: -Background) — без окна, цель держится циклом.
# Второго надзирателя не пускаем: иначе на один порт поднимутся две цели.
$script:Headless = $Background.IsPresent
$script:Mutex = $null
if ($script:Headless) {
    $script:Mutex = New-Object System.Threading.Mutex($false, 'Local\FactoryLab100-micromonitor')
    if (-not $script:Mutex.WaitOne(0)) { exit 0 }
}

$script:ErrorMarkers = @(
    'traceback', 'exception', 'syntaxerror', 'importerror', 'modulenotfounderror',
    'no module named', 'error:', 'error ', 'error,', 'error.', 'ошибка', 'ошибк',
    'сбой', 'не удалось', 'не найден', 'недоступен', 'не доступен', 'отказано',
    'failed', 'failure', 'fatal', 'critical', 'panic', 'not found'
)
$script:FalseMarkers = @('0 errors', 'errors=0', 'без ошибок', 'no errors', 'error=0')

$script:Proc = $null
$script:OutFile = $null
$script:ErrFile = $null
$script:LogFile = $null
$script:SeenOut = 0
$script:SeenErr = 0
$script:ErrorCount = 0

function Get-FactoryExe {
    param([string]$Hint)
    $candidates = @()
    if ($Hint) { $candidates += $Hint }
    $candidates += (Join-Path $PSScriptRoot 'factory.exe')
    $candidates += (Join-Path (Split-Path $PSScriptRoot -Parent) 'factory.exe')
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\FactoryLab100\factory.exe')
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) { return (Resolve-Path -LiteralPath $path).Path }
    }
    return $null
}

function Get-LogDir {
    param([string]$Hint)
    if ($Hint) { return $Hint }
    $repoLogs = Join-Path (Split-Path $PSScriptRoot -Parent) 'logs'
    if (Test-Path -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'state')) { return $repoLogs }
    return (Join-Path $env:LOCALAPPDATA 'FactoryLab100\logs')
}

function Test-IsError {
    param([string]$Text)
    $low = "$Text".ToLowerInvariant()
    foreach ($marker in $script:FalseMarkers) { if ($low.Contains($marker)) { return $false } }
    foreach ($marker in $script:ErrorMarkers) { if ($low.Contains($marker)) { return $true } }
    return $false
}

function Get-LineSource {
    param([string]$Text, [string]$Stream)
    if ($Text -match '([A-Za-z]:\\[^":\r\n]+\.(?:py|exe|ps1|cmd|bat|json|toml|md|txt|log|cfg|ini))') {
        return $Matches[1].Trim()
    }
    return $Stream
}

function Write-MonitorLine {
    param([string]$Stream, [string]$Text)
    $text = "$Text".TrimEnd("`r", "`n")
    if (-not $text) { return }
    $stamp = Get-Date -Format 'dd.MM.yyyy HH:mm:ss'
    $source = Get-LineSource -Text $text -Stream $Stream
    $isError = ($Stream -eq 'stderr') -or (Test-IsError -Text $text)
    $line = "[$stamp] $source`: $text"
    if ($isError) {
        $script:ErrorCount++
        $txtErrors.AppendText($line + [Environment]::NewLine)
    }
    $txtAll.AppendText("[$stamp] [$Stream] $line" + [Environment]::NewLine)
    try { Add-Content -LiteralPath $script:LogFile -Value $line -Encoding utf8 } catch { }
}

function Read-NewLines {
    param([string]$Path, [int]$Seen)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try {
        $all = [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)
    } catch { return @() }
    if ($all.Count -le $Seen) { return @() }
    return @($all[$Seen..($all.Count - 1)])
}

function Count-Lines {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    try { return @([System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)).Count }
    catch { return 0 }
}

function Start-Target {
    if ($script:proc -and -not $script:proc.HasExited) {
        if ($script:Headless) {
            Write-MonitorLine -Stream 'stderr' -Text "цель уже запущена (pid $($script:proc.Id)) — повторный запуск пропущен"
        }
        else {
            [System.Windows.Forms.MessageBox]::Show('Запуск уже идёт. Сначала остановите его.', 'Микромонитор')
        }
        return
    }
    $exe = Get-FactoryExe -Hint $Exe
    $workDir = $PSScriptRoot
    if ($exe) {
        $file = $exe
        $argLine = $Arguments
        $workDir = Split-Path $exe -Parent
    }
    else {
        $file = 'py'
        $argLine = "-m factory $Arguments"
        $workDir = Split-Path $PSScriptRoot -Parent
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $dir = Get-LogDir -Hint $LogDir
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $script:logFile = Join-Path $dir "micromonitor-$stamp.log"
    $script:outFile = Join-Path $dir "micromonitor-$stamp.out"
    $script:errFile = Join-Path $dir "micromonitor-$stamp.err"
    $script:seenOut = 0
    $script:seenErr = 0
    $script:errorCount = 0
    $txtErrors.Clear()
    $txtAll.Clear()
    $header = "[$(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')] запуск: $file $argLine (каталог: $workDir)"
    Add-Content -LiteralPath $script:logFile -Value $header -Encoding utf8
    $txtAll.AppendText($header + [Environment]::NewLine)
    $lblStatus.Text = "Работает: $file $argLine"
    try {
        # Окно консоли не показываем: у фабрики свой интерфейс, а вывод
        # перенаправляется в файлы журнала (пустые окна раздражают).
        $script:proc = Start-Process -FilePath $file -ArgumentList $argLine -WorkingDirectory $workDir `
            -WindowStyle Hidden -RedirectStandardOutput $script:outFile `
            -RedirectStandardError $script:errFile -PassThru
    }
    catch {
        Write-MonitorLine -Stream 'stderr' -Text "не удалось запустить: $($_.Exception.Message)"
        $lblStatus.Text = 'Запуск не удался'
        return
    }
    if ($timer) { $timer.Start() }
}

function Stop-Target {
    if ($script:proc -and -not $script:proc.HasExited) {
        try { Stop-Process -Id $script:proc.Id -Force } catch { }
        Write-MonitorLine -Stream 'stderr' -Text 'процесс остановлен пользователем'
    }
    if ($timer) { $timer.Stop() }
    $lblStatus.Text = 'Остановлено'
}

function Complete-Tick {
    foreach ($line in (Read-NewLines -Path $script:outFile -Seen $script:seenOut)) {
        Write-MonitorLine -Stream 'stdout' -Text $line
    }
    $script:seenOut = Count-Lines -Path $script:outFile
    foreach ($line in (Read-NewLines -Path $script:errFile -Seen $script:seenErr)) {
        Write-MonitorLine -Stream 'stderr' -Text $line
    }
    $script:seenErr = Count-Lines -Path $script:errFile
    if ($script:proc -and $script:proc.HasExited) {
        if ($timer) { $timer.Stop() }
        $code = $script:proc.ExitCode
        if ($code -ne 0) { Write-MonitorLine -Stream 'stderr' -Text "процесс завершился с кодом $code" }
        $lblStatus.Text = "Завершено: код $code, ошибок: $script:errorCount — журнал: $script:logFile"
    }
}

if ($script:Headless) {
    # Фоновый надзор (задача планировщика): без окна. Вместо контролов — «невидимые»
    # TextBox/Label: AppendText и Text работают и без формы, поэтому весь остальной
    # код журнала остаётся общим с оконным режимом.
    $txtErrors = New-Object System.Windows.Forms.TextBox
    $txtAll = New-Object System.Windows.Forms.TextBox
    $lblStatus = New-Object System.Windows.Forms.Label
    $timer = $null   # в фоне таймера нет: опрос идёт циклом ниже
    Start-Target
    while ($true) {
        Start-Sleep -Milliseconds 400
        Complete-Tick
        # Буферы нужны только как приёмник вывода: на диске журнал полный, память не копим.
        if ($txtAll.TextLength -gt 200000) { $txtAll.Clear(); $txtErrors.Clear() }
        if (-not $script:proc -or $script:proc.HasExited) {
            $code = if ($script:proc) { $script:proc.ExitCode } else { 'нет' }
            Write-MonitorLine -Stream 'stderr' -Text "цель завершилась (код: $code) — перезапуск через $RestartDelaySec с"
            Start-Sleep -Seconds $RestartDelaySec
            Start-Target
        }
    }
}

# --- окно ---
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Микромонитор запуска — Фабрика Lab-100'
$form.Size = New-Object System.Drawing.Size(1000, 660)
$form.StartPosition = 'CenterScreen'
$form.MinimumSize = New-Object System.Drawing.Size(700, 420)

$panel = New-Object System.Windows.Forms.Panel
$panel.Dock = 'Top'
$panel.Height = 44

$btnStart = New-Object System.Windows.Forms.Button
$btnStart.Text = 'Запустить'
$btnStart.Location = New-Object System.Drawing.Point(8, 8)
$btnStart.Size = New-Object System.Drawing.Size(100, 28)
$btnStart.Add_Click({ Start-Target })

$btnStop = New-Object System.Windows.Forms.Button
$btnStop.Text = 'Остановить'
$btnStop.Location = New-Object System.Drawing.Point(116, 8)
$btnStop.Size = New-Object System.Drawing.Size(100, 28)
$btnStop.Add_Click({ Stop-Target })

$btnLog = New-Object System.Windows.Forms.Button
$btnLog.Text = 'Открыть журнал'
$btnLog.Location = New-Object System.Drawing.Point(224, 8)
$btnLog.Size = New-Object System.Drawing.Size(120, 28)
$btnLog.Add_Click({
        if ($script:logFile -and (Test-Path -LiteralPath $script:logFile)) {
            Start-Process notepad.exe -ArgumentList "`"$script:logFile`""
        }
    })

$btnCopy = New-Object System.Windows.Forms.Button
$btnCopy.Text = 'Копировать ошибки'
$btnCopy.Location = New-Object System.Drawing.Point(352, 8)
$btnCopy.Size = New-Object System.Drawing.Size(140, 28)
$btnCopy.Add_Click({
        if ($txtErrors.TextLength -gt 0) { [System.Windows.Forms.Clipboard]::SetText($txtErrors.Text) }
    })

$btnClear = New-Object System.Windows.Forms.Button
$btnClear.Text = 'Очистить'
$btnClear.Location = New-Object System.Drawing.Point(500, 8)
$btnClear.Size = New-Object System.Drawing.Size(100, 28)
$btnClear.Add_Click({ $txtErrors.Clear(); $txtAll.Clear() })

$panel.Controls.AddRange(@($btnStart, $btnStop, $btnLog, $btnCopy, $btnClear))

$split = New-Object System.Windows.Forms.SplitContainer
$split.Dock = 'Fill'
$split.Orientation = 'Horizontal'
$split.SplitterDistance = 260

$errorsBox = New-Object System.Windows.Forms.GroupBox
$errorsBox.Text = 'Ошибки (столбиком: дата, время, источник, текст)'
$errorsBox.Dock = 'Fill'
$txtErrors = New-Object System.Windows.Forms.TextBox
$txtErrors.Multiline = $true
$txtErrors.ReadOnly = $true
$txtErrors.ScrollBars = 'Vertical'
$txtErrors.WordWrap = $false
$txtErrors.Dock = 'Fill'
$txtErrors.Font = New-Object System.Drawing.Font('Consolas', 9)
$txtErrors.BackColor = [System.Drawing.Color]::FromArgb(255, 248, 248)
$errorsBox.Controls.Add($txtErrors)

$allBox = New-Object System.Windows.Forms.GroupBox
$allBox.Text = 'Весь вывод'
$allBox.Dock = 'Fill'
$txtAll = New-Object System.Windows.Forms.TextBox
$txtAll.Multiline = $true
$txtAll.ReadOnly = $true
$txtAll.ScrollBars = 'Vertical'
$txtAll.WordWrap = $false
$txtAll.Dock = 'Fill'
$txtAll.Font = New-Object System.Drawing.Font('Consolas', 9)
$allBox.Controls.Add($txtAll)

$split.Panel1.Controls.Add($errorsBox)
$split.Panel2.Controls.Add($allBox)

$status = New-Object System.Windows.Forms.StatusStrip
$lblStatus = New-Object System.Windows.Forms.ToolStripStatusLabel
$lblStatus.Text = 'Готово'
$status.Items.Add($lblStatus) | Out-Null

$form.Controls.Add($split)
$form.Controls.Add($panel)
$form.Controls.Add($status)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 400
$timer.Add_Tick({ Complete-Tick })

$form.Add_Shown({ if ($AutoStart) { Start-Target } })

[void]$form.ShowDialog()
