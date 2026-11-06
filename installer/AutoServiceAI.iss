; Установщик AutoService AI (Inno Setup 6). Сборка: ISCC.exe /DAppVersion=1.0.0 installer\AutoServiceAI.iss
; Данные программы хранятся отдельно от файлов программы и не удаляются при обновлении и удалении.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "AutoService AI"
#define AppExe "AutoServiceAI.exe"

[Setup]
AppId={{8F3C2B71-5C1E-4C55-9B7E-3A2D9E6F1A10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AutoService AI
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AutoServiceAI-Setup
SetupIconFile=..\src\autoservice\assets\autoservice.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
MinVersion=10.0.17763
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=Установщик {#AppName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Dirs]
Name: "{code:DataDir}"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{code:DataDir}\backups"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{code:DataDir}\backups\before-update"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "..\dist\AutoServiceAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifexist "..\build\ocr\tesseract.exe"
; Необязательный компонент: Tesseract OCR (Apache 2.0), добавляется при сборке, если подготовлен.
Source: "..\build\ocr\*"; DestDir: "{app}\ocr"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[INI]
Filename: "{app}\autoservice.ini"; Section: "paths"; Key: "data_dir"; String: "{code:DataDir}"

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{autoprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName}\Папка данных AutoService AI"; Filename: "{code:DataDir}"
Name: "{autoprograms}\{#AppName}\Удалить {#AppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\autoservice.ini"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
function DataDir(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := ExpandConstant('{commonappdata}\AutoServiceAI')
  else
    Result := ExpandConstant('{localappdata}\AutoServiceAI');
end;

{ Перед обновлением копируем базу (и журнал WAL), если она уже есть. }
procedure BackupDatabaseBeforeUpdate();
var
  Db, Stamp, Dest: String;
begin
  Db := DataDir('') + '\data\autoservice.db';
  if FileExists(Db) then
  begin
    Stamp := GetDateTimeString('yyyymmdd-hhnnss', '-', '-');
    Dest := DataDir('') + '\backups\before-update\installer-' + Stamp;
    ForceDirectories(DataDir('') + '\backups\before-update');
    if FileCopy(Db, Dest + '.db', False) then
    begin
      if FileExists(Db + '-wal') then
        FileCopy(Db + '-wal', Dest + '.db-wal', False);
      Log('Копия базы перед обновлением: ' + Dest + '.db');
    end
    else
      Log('Не удалось скопировать базу перед обновлением');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    BackupDatabaseBeforeUpdate();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
    MsgBox('Программа удалена. Рабочие данные (база, вложения, резервные копии) сохранены в папке:' + #13#10 +
           DataDir('') + #13#10 + 'Удалите её вручную, только если данные больше не нужны.', mbInformation, MB_OK);
end;
