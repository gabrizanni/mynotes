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

$workspace = Join-Path ([IO.Path]::GetTempPath()) "MyNotes UI tests $([Guid]::NewGuid().ToString('N'))"
$oldWorkspace = $env:MYNOTES_WORKSPACE
$oldPython = $env:MYNOTES_PYTHON
$process = $null
$script:pattern = $null

try {
    $env:MYNOTES_WORKSPACE = $workspace
    $env:MYNOTES_PYTHON = $runtime
    $process = Start-Process -FilePath $desktop -PassThru
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

    Invoke-Control (Find-ById 'NavActivity')
    $null = Find-ById 'QueuePause'
    Invoke-Control (Find-ById 'NavSettings')
    $null = Find-ById 'SettingsProvider'
    $null = Find-ById 'SettingsWordVerify'

    'Desktop UI smoke test passed.'
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        $null = $process.CloseMainWindow()
        if (-not $process.WaitForExit(6000)) { $process.Kill() }
    }
    $env:MYNOTES_WORKSPACE = $oldWorkspace
    $env:MYNOTES_PYTHON = $oldPython
    if (Test-Path -LiteralPath $workspace) {
        Remove-Item -LiteralPath $workspace -Recurse -Force
    }
}
