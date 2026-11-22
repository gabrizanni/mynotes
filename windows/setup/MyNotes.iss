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
UninstallDisplayIcon={app}\desktop\MyNotes.Desktop.exe
LicenseFile={#StageDir}\LICENSE

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MyNotes"; Filename: "{app}\desktop\MyNotes.Desktop.exe"; WorkingDir: "{app}\desktop"

[Code]
function SetupMayProceed: String;
begin
  Result := '';
  { Replacement of an existing install must wait for an idle workspace and
    coordinate with the background recorder. Until that contract is tested,
    restrict internal candidates to clean installs. }
  if FileExists(ExpandConstant('{app}\runtime\mynotes.exe')) then
    Result := 'This internal build cannot upgrade an installed copy of MyNotes.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := SetupMayProceed;
end;
