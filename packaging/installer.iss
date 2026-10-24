; Inno Setup script - builds dist\DepositSlipTracker-Setup-<version>.exe
; Run through build.py (which passes the version), or: iscc /DMyAppVersion=1.0.0 installer.iss

#define MyAppName "Deposit Slip Tracker"
#define MyAppExe "DepositSlipTracker.exe"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{6C1E8F4A-3B7D-4C2E-9A51-D0F2B8E7C613}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppName}
; Per-user install: no administrator rights needed. {autopf} becomes %LOCALAPPDATA%\Programs.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=DepositSlipTracker-Setup-{#MyAppVersion}
SetupIconFile=..\deposit_tracker\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Clear libraries from the previous version before upgrading in place.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\DepositSlipTracker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; The data folder (Documents\DepositSlips) and settings are never touched by uninstall.
