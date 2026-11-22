; Windows installer: SwimTunes-Setup.exe
;   iscc /DAppVersion=0.8.13 /DSourceExe=path\to\SwimTunes.exe packaging\windows-installer.iss
; Installs for the current user only (no administrator password), adds SwimTunes to the
; Start menu and the desktop, and to Settings > Apps for uninstalling. The music folder
; (Music\SwimTunes) is never touched by installing or uninstalling.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceExe
  #define SourceExe "..\dist\SwimTunes.exe"
#endif

[Setup]
AppId={{6C1F3B0E-6E1A-4E57-9E43-5157494D5455}
AppName=SwimTunes
AppVersion={#AppVersion}
AppPublisher=SwimTunes
AppPublisherURL=https://github.com/jumalley/swimtunes
AppSupportURL=https://github.com/jumalley/swimtunes#questions
DefaultDirName={localappdata}\Programs\SwimTunes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SwimTunes-Setup
SetupIconFile=..\swimtunes\static\icon.ico
UninstallDisplayIcon={app}\SwimTunes.exe
UninstallDisplayName=SwimTunes
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "pt"; MessagesFile: "compiler:Languages\Portuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "SwimTunes.exe"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\SwimTunes"; Filename: "{app}\SwimTunes.exe"
Name: "{userdesktop}\SwimTunes"; Filename: "{app}\SwimTunes.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SwimTunes.exe"; Description: "{cm:LaunchProgram,SwimTunes}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop SwimTunes if it is running, so it can be removed
Filename: "{cmd}"; Parameters: "/c taskkill /im SwimTunes.exe /f"; Flags: runhidden; RunOnceId: "StopSwimTunes"

[UninstallDelete]
; files left next to the program by updates
Type: files; Name: "{app}\SwimTunes.old.exe"
Type: files; Name: "{app}\SwimTunes.new.exe"
