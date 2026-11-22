#ifndef StageDir
  #error StageDir must refer to a verified build
#endif
#ifndef OutputFolder
  #error OutputFolder is required
#endif
#ifndef MyNotesVersion
  #error MyNotesVersion is required
#endif

[Setup]
AppId=MyNotes.Windows.Desktop
AppName=MyNotes
AppVersion={#MyNotesVersion}
DefaultDirName={localappdata}\Programs\MyNotes
DefaultGroupName=MyNotes
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.22000
OutputDir={#OutputFolder}
OutputBaseFilename=MyNotes-Setup-x64-internal
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AppMutex=MyNotes.Windows.Desktop.Running
UninstallDisplayIcon={app}\desktop\MyNotes.Desktop.exe
LicenseFile={#StageDir}\LICENSE

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MyNotes"; Filename: "{app}\desktop\MyNotes.Desktop.exe"; WorkingDir: "{app}\desktop"

[Code]
const
  InvalidHandle = -1;
  BusyMessage = 'MyNotes Capture is recording or exporting audio, or another installer is running. Wait for it to finish and try again.';

var
  CaptureUpdateHandle: Int64;
  RestartCapture: Boolean;

function OpenCaptureLease(const FileName: String; DesiredAccess, ShareMode: LongWord;
  SecurityAttributes: Int64; CreationDisposition, Flags: LongWord;
  TemplateFile: Int64): Int64;
  external 'CreateFileW@kernel32.dll stdcall';

function CloseCaptureLease(Handle: Int64): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function CaptureHome: String;
begin
  Result := ExpandConstant('{localappdata}\MyNotes\Capture');
end;

function AcquireCaptureLease: Boolean;
begin
  Result := CaptureUpdateHandle <> 0;
  if Result then
    Exit;
  if not ForceDirectories(CaptureHome) then
    Exit;
  { Share mode 0 excludes both existing and future Capture file handles.
    Keep the handle open until the installer finishes replacing the files. }
  CaptureUpdateHandle := OpenCaptureLease(CaptureHome + '\update.lock',
    $40000000, 0, 0, 4, $80, 0);
  if CaptureUpdateHandle = InvalidHandle then
    CaptureUpdateHandle := 0;
  Result := CaptureUpdateHandle <> 0;
end;

procedure ReleaseCaptureLease;
begin
  if CaptureUpdateHandle <> 0 then
  begin
    CloseCaptureLease(CaptureUpdateHandle);
    CaptureUpdateHandle := 0;
  end;
end;

function MaintainCapture(const Action: String; var State: String): Boolean;
var
  Python, CaptureExecutable, Params: String;
  ExitCode: Integer;
  Output: TExecOutput;
begin
  Python := ExpandConstant('{app}\runtime\python.exe');
  CaptureExecutable := ExpandConstant('{app}\capture\MyNotes.Capture.exe');
  Params := '-I -m mynotes.capture_windows maintenance ' + Action +
    ' --capture-exe "' + CaptureExecutable + '" --capture-home "' + CaptureHome + '"';
  State := '';
  Result := ExecAndCaptureOutput(Python, Params, ExpandConstant('{app}'),
    SW_HIDE, ewWaitUntilTerminated, ExitCode, Output);
  if not Result then
    Exit;
  if GetArrayLength(Output.StdOut) = 1 then
    State := Trim(Output.StdOut[0]);
  Result := ExitCode = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  State: String;
begin
  Result := '';
  if not AcquireCaptureLease then
  begin
    Result := BusyMessage;
    Exit;
  end;
  if FileExists(ExpandConstant('{app}\runtime\python.exe')) and not RestartCapture then
  begin
    if not MaintainCapture('quiesce', State) or
      ((State <> 'ENABLED') and (State <> 'DISABLED') and (State <> 'ABSENT')) then
    begin
      ReleaseCaptureLease;
      Result := 'Setup could not pause the installed MyNotes Capture task. See the Setup log.';
      Exit;
    end;
    RestartCapture := State = 'ENABLED';
  end;
end;

procedure DeinitializeSetup;
var
  State: String;
begin
  ReleaseCaptureLease;
  if RestartCapture and not MaintainCapture('resume', State) then
    RaiseException('Setup could not restart MyNotes Capture. Enable Capture again in MyNotes.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  State: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    if not AcquireCaptureLease then
      RaiseException(BusyMessage);
    if not MaintainCapture('remove', State) then
      RaiseException('Uninstall could not remove the MyNotes Capture task. See the Setup log.');
  end;
end;

procedure DeinitializeUninstall;
begin
  ReleaseCaptureLease;
end;
