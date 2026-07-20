; WebJam Windows x64 installer (Inno Setup 6.3+).
;
; The CI/release caller should override the values below with ISCC /D defines.
; Keeping defaults here makes local compilation predictable while the stable
; AppId makes upgrades replace the same per-user installation.

#ifndef WebJamAppName
  #define WebJamAppName "WebJam"
#endif

#ifndef WebJamAppVersion
  #define WebJamAppVersion "0.0.0"
#endif

; Keep this numeric (up to four dot-separated components). Override it
; separately if WebJamAppVersion ever carries a prerelease suffix.
#ifndef WebJamVersionInfoVersion
  #define WebJamVersionInfoVersion WebJamAppVersion
#endif

#ifndef WebJamAppPublisher
  #define WebJamAppPublisher "WebJam Contributors"
#endif

#ifndef WebJamAppId
  #define WebJamAppId "{{39B63AB8-F1B2-4E8E-9E70-35F517800BA5}"
#endif

#ifndef WebJamUninstallKeyName
  #define WebJamUninstallKeyName "{39B63AB8-F1B2-4E8E-9E70-35F517800BA5}_is1"
#endif

#ifndef WebJamExeName
  #define WebJamExeName "WebJam.exe"
#endif

#ifndef WebJamSourceDir
  #define WebJamSourceDir "..\..\dist\WebJam"
#endif

#ifndef WebJamOutputDir
  #define WebJamOutputDir "..\..\out"
#endif

#ifndef WebJamOutputBaseFilename
  #define WebJamOutputBaseFilename "WebJam-Setup-x64"
#endif

[Setup]
AppId={#WebJamAppId}
AppName={#WebJamAppName}
AppVersion={#WebJamAppVersion}
AppVerName={#WebJamAppName} {#WebJamAppVersion}
AppPublisher={#WebJamAppPublisher}
AppPublisherURL=https://github.com/rupret007/webjam
AppSupportURL=https://github.com/rupret007/webjam/issues
AppUpdatesURL=https://github.com/rupret007/webjam/releases
AppReadmeFile={app}\README-WINDOWS.txt
AppCopyright=Copyright (c) 2024 WebJam Contributors
DefaultDirName={localappdata}\Programs\{#WebJamAppName}
DefaultGroupName={#WebJamAppName}
UninstallDisplayName={#WebJamAppName} {#WebJamAppVersion}
UninstallDisplayIcon={app}\{#WebJamExeName}
OutputDir={#WebJamOutputDir}
OutputBaseFilename={#WebJamOutputBaseFilename}
SetupIconFile=..\..\webjam_qt\theme\assets\webjam.ico
LicenseFile=..\..\LICENSE
InfoBeforeFile=README-WINDOWS.txt
VersionInfoVersion={#WebJamVersionInfoVersion}
VersionInfoProductVersion={#WebJamVersionInfoVersion}
VersionInfoTextVersion={#WebJamAppVersion}
VersionInfoProductTextVersion={#WebJamAppVersion}
VersionInfoDescription={#WebJamAppName} Setup
VersionInfoProductName={#WebJamAppName}
VersionInfoCompany={#WebJamAppPublisher}
VersionInfoOriginalFileName={#WebJamOutputBaseFilename}.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
AllowNoIcons=no
UsePreviousAppDir=yes
UsePreviousTasks=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
ChangesAssociations=no

; For publisher builds, define WebJamSignTool to the name configured with
; ISCC's /S option. Inno then signs both Setup and its embedded uninstaller.
#ifdef WebJamSignTool
SignTool={#WebJamSignTool}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Every wildcarded file is private to this application. Inno records these
; exact files for uninstall; intentionally do not add a broad UninstallDelete
; rule, so user-created content and WebJam's profile data are preserved.
Source: "{#WebJamSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; DestName: "THIRD_PARTY_NOTICES.md"; Flags: ignoreversion
Source: "README-WINDOWS.txt"; DestDir: "{app}"; DestName: "README-WINDOWS.txt"; Flags: ignoreversion

[InstallDelete]
; A PyInstaller onedir update must not overlay a new _internal tree on an old
; one: modules removed by the new build could otherwise survive the upgrade.
; Delete only WebJam-owned paths so an unrelated file in {app} is preserved.
Type: filesandordirs; Name: "{app}\_internal"; Check: IsVerifiedExistingWebJamInstall
Type: files; Name: "{app}\{#WebJamExeName}"; Check: IsVerifiedExistingWebJamInstall
Type: files; Name: "{app}\webjam-fabric.exe"; Check: IsVerifiedExistingWebJamInstall
Type: files; Name: "{app}\webjam-fabric.sha256"; Check: IsVerifiedExistingWebJamInstall
Type: files; Name: "{app}\LICENSE.txt"; Check: IsVerifiedExistingWebJamInstall
Type: files; Name: "{app}\THIRD_PARTY_NOTICES.md"; Check: IsVerifiedExistingWebJamInstall
Type: files; Name: "{app}\README-WINDOWS.txt"; Check: IsVerifiedExistingWebJamInstall

[Icons]
Name: "{autoprograms}\{#WebJamAppName}"; Filename: "{app}\{#WebJamExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#WebJamExeName}"
Name: "{autodesktop}\{#WebJamAppName}"; Filename: "{app}\{#WebJamExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#WebJamExeName}"; Tasks: desktopicon

; Deliberately no [Run] section: installation never starts WebJam or another
; bundled executable without a separate, explicit action from the user.

[Code]
function IsVerifiedExistingWebJamInstall(): Boolean;
var
  ExistingLocation: String;
  ExpectedLocation: String;
  ExistingExecutable: String;
begin
  Result := RegQueryStringValue(
    HKEY_CURRENT_USER,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#WebJamUninstallKeyName}',
    'InstallLocation',
    ExistingLocation
  );
  if not Result then
    exit;

  ExpectedLocation := ExpandConstant('{app}');
  ExistingExecutable := AddBackslash(ExistingLocation) + '{#WebJamExeName}';
  Result :=
    (CompareText(AddBackslash(ExistingLocation), AddBackslash(ExpectedLocation)) = 0) and
    FileExists(ExistingExecutable);
end;
