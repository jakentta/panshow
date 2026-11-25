; Inno Setup recipe for the Panshow installer.
; Built by build.ps1, which passes /DAppVersion=x.y.z
#define AppName "Panshow"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E0B7C3A-9F41-4C77-9A8E-2B5D0F1C7A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL=https://github.com/jakentta/panshow
AppSupportURL=https://github.com/jakentta/panshow
AppUpdatesURL=https://github.com/jakentta/panshow/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany={#AppName}
; Installs for the current user only: no administrator rights, and updates can replace the
; program files without asking for permission.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\panshow\panshow.ico
UninstallDisplayIcon={app}\{#AppName}.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; remove the previous version's program files first, so old and new never mix
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{group}\{#AppName} settings"; Filename: "{app}\{#AppName}.exe"; Parameters: "--settings"
Name: "{group}\Read me"; Filename: "{app}\README.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; after an automatic update that was installed at start-up, start Panshow again
Filename: "{app}\{#AppName}.exe"; Flags: nowait; Check: RelaunchAfterUpdate

[UninstallDelete]
; downloaded updates and the backup copy (settings and face memory are kept)
Type: filesandordirs; Name: "{localappdata}\{#AppName}\updates"

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (Pos('/RELAUNCH', Uppercase(GetCmdTail)) > 0);
end;
