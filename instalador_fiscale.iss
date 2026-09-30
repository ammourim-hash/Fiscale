; Instalador do Fiscale (Inno Setup)
; Compilar: "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" instalador_fiscale.iss
; Gera: dist\FiscaleInstalador.exe

#define MyApp "Fiscale"
#define MyVersion "1.1"
#define MyExe "Fiscale.exe"

[Setup]
AppName={#MyApp}
AppVersion={#MyVersion}
AppPublisher=MONTE ASSESSORIA
DefaultDirName={autopf}\Fiscale
DefaultGroupName=Fiscale
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=FiscaleInstalador
SetupIconFile=fiscale.ico
UninstallDisplayIcon={app}\{#MyExe}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "dist\{#MyExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "COMO_USAR.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
; O atalho da Area de Trabalho e criado SEMPRE (antes era uma caixinha opcional
; e passava batido, deixando a maquina sem icone).
Name: "{group}\Fiscale"; Filename: "{app}\{#MyExe}"
Name: "{group}\Como usar"; Filename: "{app}\COMO_USAR.txt"
Name: "{group}\Desinstalar o Fiscale"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Fiscale"; Filename: "{app}\{#MyExe}"

[Run]
Filename: "{app}\{#MyExe}"; Description: "Abrir o Fiscale agora"; Flags: nowait postinstall skipifsilent
