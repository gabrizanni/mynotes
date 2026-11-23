# Run on an interactive Windows 11 desktop with the packaged Python environment.
# powershell.exe -NoProfile -STA -File .\windows\desktop\tests\SmokeTest.ps1 -DesktopPath .\stage\desktop\MyNotes.Desktop.exe
param(
    [Parameter(Mandatory = $true)][string]$DesktopPath,
    [string]$PythonPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

function Wait-Until([scriptblock]$Probe, [string]$Description) {
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    do {
        $found = & $Probe
        if ($null -ne $found) { return $found }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Description"
}

function Find-ById([string]$ControlId) {
    $condition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::AutomationIdProperty, $ControlId)
    return Wait-Until {
        $window.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
    } "control $ControlId"
}

function Invoke-Control($Element) {
    if (-not $Element.Current.IsEnabled) {
        throw "The control '$($Element.Current.AutomationId)' is disabled."
    }
    $invoke = [Windows.Automation.InvokePattern]::Pattern
    if ($Element.TryGetCurrentPattern($invoke, [ref]$script:pattern)) {
        $script:pattern.Invoke()
        return
    }
    $select = [Windows.Automation.SelectionItemPattern]::Pattern
    if ($Element.TryGetCurrentPattern($select, [ref]$script:pattern)) {
        $script:pattern.Select()
        return
    }
    throw "The control '$($Element.Current.AutomationId)' does not support invoke or selection."
}

$desktop = (Resolve-Path -LiteralPath $DesktopPath).Path
$runtime = if ($PythonPath) {
    (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    Join-Path (Split-Path (Split-Path $desktop -Parent) -Parent) 'runtime\python.exe'
}
if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) {
    throw 'Staged Python runtime is missing.'
}
& $runtime -I -X utf8 -m mynotes --version
if ($LASTEXITCODE -ne 0) {
    throw 'The packaged Python runtime could not launch MyNotes in isolated mode.'
}

$workspace = Join-Path ([IO.Path]::GetTempPath()) "MyNotes UI tests $([Guid]::NewGuid().ToString('N'))"
$oldWorkspace = $env:MYNOTES_WORKSPACE
$oldPython = $env:MYNOTES_PYTHON
$oldTrace = $env:MYNOTES_DESKTOP_TEST_TRACE
$trace = Join-Path (Split-Path $workspace -Parent) "mynotes-desktop-$([Guid]::NewGuid().ToString('N')).log"
$startup = Get-Date
$process = $null
$window = $null
$script:pattern = $null

try {
    $env:MYNOTES_WORKSPACE = $workspace
    $env:MYNOTES_PYTHON = $runtime
    $env:MYNOTES_DESKTOP_TEST_TRACE = $trace
    $process = Start-Process -FilePath $desktop -WorkingDirectory (Split-Path $desktop -Parent) -PassThru
    $window = Wait-Until {
        $process.Refresh()
        if ($process.HasExited) { throw "Desktop exited with code $($process.ExitCode)." }
        if ($process.MainWindowHandle -ne [IntPtr]::Zero) {
            [Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
        }
    } 'the MyNotes window'

    $null = Find-ById 'LibrarySearch'
    Invoke-Control (Find-ById 'NavCourses')
    Invoke-Control (Find-ById 'CourseAdd')
    $entry = Find-ById 'CourseName'
    $name = 'Synthetic Windows course'
    $entry.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($name)
    $primary = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty, 'Aggiungi')
    $add = Wait-Until {
        $window.FindFirst([Windows.Automation.TreeScope]::Descendants, $primary)
    } 'the Add confirmation button'
    Invoke-Control $add
    $null = Find-ById 'CourseProfile'
    $config = & $runtime -X utf8 -m mynotes config show --workspace $workspace --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not ($config.courses.name -contains $name)) {
        throw 'The course created through the UI was not written to the workspace.'
    }

    Invoke-Control (Find-ById 'NavCalendar')
    Invoke-Control (Find-ById 'CaptureConfigure')
    $null = Find-ById 'CaptureEnable'
    $capture = & $runtime -X utf8 -m mynotes capture dashboard --workspace $workspace --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $capture.configured) {
        throw 'Capture configuration did not persist.'
    }
    $sourcePath = Join-Path $workspace 'synthetic-calendar.json'
    $source = @{
        sourceID = 'synthetic-ui-source'
        url = 'https://example.invalid/my-notes.ics'
        label = 'Synthetic calendar'
        timezone = 'Europe/Rome'
        periodStart = '2030-06-01'
        periodEnd = '2030-07-01'
        importMode = 'mapped'
        mappings = @(@{ match = 'Synthetic lecture'; courseCode = $config.courses[0].code; firstLesson = 1 })
    } | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($sourcePath, $source)
    & $runtime -X utf8 -m mynotes capture subscribe --file $sourcePath --workspace $workspace --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the synthetic calendar.' }
    # The source was added externally while this Calendar page was already open.
    Invoke-Control (Find-ById 'CalendarSourceRepair')
    $endField = Find-ById 'CalendarSourceTo'
    if ($endField.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value -ne '2030-06-30') {
        throw 'The repair form did not retain the calendar-local inclusive end date.'
    }
    $cancel = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty, 'Annulla')
    Invoke-Control (Wait-Until {
        $window.FindFirst([Windows.Automation.TreeScope]::Descendants, $cancel)
    } 'the Calendar cancel button')
    $null = Wait-Until {
        if ($window.Current.IsEnabled -and (Find-ById 'Refresh').Current.IsEnabled) { $true }
    } 'the Calendar repair dialog to close'

    Invoke-Control (Find-ById 'NavActivity')
    $pause = Find-ById 'QueuePause'
    & $runtime -X utf8 -m mynotes transcription-queue pause --workspace $workspace --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare a queue status change.' }
    $null = Wait-Until {
        if ($pause.Current.Name -eq 'Riprendi coda') { $true }
    } 'the Activity page to reflect the external queue pause'
    & $runtime -X utf8 -m mynotes transcription-queue configure --parallel 2 --workspace $workspace --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the transcription preference.' }
    Invoke-Control (Find-ById 'NavSettings')
    $null = Find-ById 'SettingsProvider'
    $null = Find-ById 'SettingsProviderLogin'
    $null = Find-ById 'SettingsWordVerify'
    $parallel = Find-ById 'SettingsSpeechParallel'
    $selection = $parallel.GetCurrentPattern([Windows.Automation.SelectionPattern]::Pattern).Current.GetSelection()
    if ($selection.Count -ne 1 -or $selection[0].Current.Name -ne '2') {
        throw 'The saved transcription preference was not reflected in Settings.'
    }

} catch {
    if (Test-Path -LiteralPath $trace) {
        Get-Content -LiteralPath $trace
    }
    if ($null -ne $window -and -not $process.HasExited) {
        $window.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition) |
            Select-Object -First 40 |
            ForEach-Object { "$($_.Current.AutomationId) $($_.Current.Name)" }
    }
    Get-WinEvent -FilterHashtable @{LogName = 'Application'; Id = 1000, 1001, 1026; StartTime = $startup} -ErrorAction SilentlyContinue |
        Where-Object Message -like '*MyNotes.Desktop.exe*' | Select-Object -First 5 TimeCreated, Id, Message | Format-List
    throw
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        $null = $process.CloseMainWindow()
        if (-not $process.WaitForExit(6000)) {
            $process.Kill()
            $null = $process.WaitForExit(3000)
        }
    }
    $env:MYNOTES_WORKSPACE = $oldWorkspace
    $env:MYNOTES_PYTHON = $oldPython
    $env:MYNOTES_DESKTOP_TEST_TRACE = $oldTrace
    Remove-Item -LiteralPath $trace -ErrorAction SilentlyContinue
    # The closing window may leave a short-lived Python child holding the
    # workspace as its current directory. Wait for that handle before removal.
    for ($attempt = 0; (Test-Path -LiteralPath $workspace); $attempt++) {
        try {
            Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction Stop
        } catch [IO.IOException] {
            if ($attempt -ge 24) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

'Desktop UI smoke test passed.'
