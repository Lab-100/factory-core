; Установщик «Фабрика Lab-100» для Windows (Inno Setup 6).
;
; Собирается скриптом scripts\make_installer.ps1 (передаёт /DAppVersion=<версия>).
; Ставится в профиль пользователя (%LOCALAPPDATA%\Programs\FactoryLab100), без
; прав администратора. Программа регистрируется в «Программы и компоненты»
; (AppId), удаляется штатным деинсталлятором (unins000.exe); ярлыки, запись
; автозапуска и каталог в PATH удаляются вместе с программой.

#ifndef AppVersion
  #define AppVersion "0.4.1"
#endif

#define AppName "Фабрика Lab-100"
#define AppExeName "factory.exe"

[Setup]
AppId={{8F3B1E64-2C4D-4F77-B1E9-7A5C0D3E9A21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Lab-100
AppPublisherURL=https://github.com/Lab-100/factory-core
AppSupportURL=https://github.com/Lab-100/factory-core
AppUpdatesURL=https://github.com/Lab-100/factory-core
DefaultDirName={localappdata}\Programs\FactoryLab100
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=factory-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "path"; Description: "Добавить каталог фабрики в PATH (запуск factory из любого терминала)"; Flags: checkedonce
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: checkedonce
Name: "autostart"; Description: "Запускать дашборд при входе в систему"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\schemas\*"; DestDir: "{app}\schemas"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\examples\*"; DestDir: "{app}\examples"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\project.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; Инструменты фабрики: скрипты установки/сборки, шим, схема установщика.
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\Фабрика Lab-100 (окно)"; Filename: "{app}\{#AppExeName}"; Parameters: "ui --mode desktop"; WorkingDir: "{app}"
Name: "{group}\Дашборд в браузере"; Filename: "{app}\{#AppExeName}"; Parameters: "ui --mode web"; WorkingDir: "{app}"
Name: "{group}\Состояние фабрики"; Filename: "{app}\{#AppExeName}"; Parameters: "status"; WorkingDir: "{app}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Фабрика Lab-100"; Filename: "{app}\{#AppExeName}"; Parameters: "ui --mode desktop"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: path; Check: NeedsAddPath('{app}')
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "FactoryLab100"; ValueData: """{app}\{#AppExeName}"" ui --mode web"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "status"; Description: "Проверить установку"; Flags: postinstall nowait skipifsilent

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure RemoveFromPath();
var
  OrigPath, Rebuilt, Item, Target: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then
    exit;
  Target := ExpandConstant('{app}');
  OrigPath := OrigPath + ';';
  Rebuilt := '';
  while True do
  begin
    P := Pos(';', OrigPath);
    if P = 0 then
      break;
    Item := Copy(OrigPath, 1, P - 1);
    Delete(OrigPath, 1, P);
    if (Item <> '') and (CompareText(Item, Target) <> 0) then
    begin
      if Rebuilt <> '' then
        Rebuilt := Rebuilt + ';';
      Rebuilt := Rebuilt + Item;
    end;
  end;
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Rebuilt);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath();
end;
