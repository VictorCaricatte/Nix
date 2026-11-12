; Instalador do Nix (Inno Setup 6).  Compile com installer\build_installer.ps1
; ou abra este arquivo no Inno Setup Compiler (a pasta dist_installer\Nix
; precisa existir: é gerada pelo PyInstaller).

#define MyAppName "Nix"
#ifndef MyAppVersion
  #define MyAppVersion "1.3.0"
#endif
#define MyAppExe "Nix.exe"

[Setup]
AppId={{858A54FD-C15E-43E1-8663-1333B00299C3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Nix
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Instala só para o usuário atual (sem pedir administrador). O assistente
; deixa escolher "para todos os usuários" se quiserem.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\installer_output
OutputBaseFilename=Nix_Setup_{#MyAppVersion}
SetupIconFile=..\Nix\Nix.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Nix Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist_installer\Nix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; As sessões salvas ficam em %APPDATA%\Nix\config.json e NÃO são removidas na
; desinstalação (para não perder dados ao reinstalar/atualizar).
