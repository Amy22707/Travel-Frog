#if Edition == "gallery"
  #define AppFolder "TravelFrogGallery"
  #define AppTitle "旅行青蛙·中国之旅图鉴版"
  #define Launcher "FrogGallery.exe"
  #define OtherLauncher "FrogNormal.exe"
  #define WindowAppId "TravelFrog.Gallery"
#else
  #define AppFolder "TravelFrogNormal"
  #define AppTitle "旅行青蛙·中国之旅"
  #define Launcher "FrogNormal.exe"
  #define OtherLauncher "FrogGallery.exe"
  #define WindowAppId "TravelFrog.Normal"
#endif

[Setup]
AppId={#AppFolder}
AppName={#AppTitle}
AppVersion={#AppVersion}
AppPublisher=Amy Studio
AppPublisherURL=https://amy22707.github.io/studio/travel-frog/
DefaultDirName={localappdata}\TravelFrog\{#AppFolder}
DefaultGroupName={#AppTitle}
DisableDirPage=no
DisableProgramGroupPage=yes
DirExistsWarning=no
AppendDefaultDirName=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#RootDir}\release
OutputBaseFilename={#AppFolder}-Setup
SetupIconFile={#RootDir}\.build\icon.ico
UninstallDisplayIcon={app}\{#Launcher}
WizardStyle=modern
WizardSizePercent=140,140
WizardResizable=yes
DisableWelcomePage=no
Compression=lzma2/fast
SolidCompression=no
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
UninstallDisplayName={#AppTitle}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#GameDir}\{#Launcher}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GameDir}\FrogServer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GameDir}\icon.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GameDir}\使用说明.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GameDir}\存档管理.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GameDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#GameDir}\web\*"; DestDir: "{app}\web"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppTitle}"; Filename: "{app}\{#Launcher}"; WorkingDir: "{app}"; AppUserModelID: "{#WindowAppId}"
Name: "{userdesktop}\{#AppTitle}"; Filename: "{app}\{#Launcher}"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "{#WindowAppId}"

[Run]
Filename: "{app}\{#Launcher}"; Description: "{cm:LaunchProgram,{#AppTitle}}"; Flags: nowait postinstall skipifsilent

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and FileExists(AddBackslash(WizardDirValue) + '{#OtherLauncher}') then
  begin
    if ActiveLanguage = 'chinesesimplified' then
      MsgBox('普通版和图鉴版需要安装到不同文件夹，请选择其他安装位置。', mbError, MB_OK)
    else
      MsgBox('Install the Normal and Gallery editions in separate folders. Please choose another location.', mbError, MB_OK);
    Result := False;
  end;
end;
