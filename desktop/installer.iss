; SPDX-License-Identifier: AGPL-3.0-only
; Build inputs are explicitly supplied by build-installer.mjs.
#ifndef AppVersion
  #error AppVersion must be provided
#endif
#ifndef AppDirectory
  #error AppDirectory must be provided
#endif
#ifndef OutputDirectory
  #error OutputDirectory must be provided
#endif

[Setup]
AppId={{7FA5B648-94A8-4A1C-A9BE-E812A611D83D}
AppName=BirdShot
AppVersion={#AppVersion}
AppPublisher=BirdShot contributors
AppPublisherURL=https://github.com/HicoderDR/BirdShot
DefaultDirName={localappdata}\Programs\BirdShot
DefaultGroupName=BirdShot
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupArchitecture=x64
MinVersion=10.0
DisableDirPage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
OutputDir={#OutputDirectory}
OutputBaseFilename=BirdShot-{#AppVersion}-Setup-x64
Compression=zip/1
SolidCompression=no
WizardStyle=modern
SetupLogging=no
UninstallDisplayIcon={app}\BirdShot.exe
UninstallDisplayName=BirdShot
LicenseFile={#AppDirectory}\resources\licenses\AGPL-3.0.txt
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
RestartIfNeededByRun=no
DisableReadyPage=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#AppDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\BirdShot\BirdShot"; Filename: "{app}\BirdShot.exe"; WorkingDir: "{app}"
Name: "{userprograms}\BirdShot\卸载 BirdShot"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\BirdShot.exe"; Description: "打开 BirdShot"; Flags: nowait postinstall skipifsilent

; Deliberately no UninstallDelete: Inno removes only logged installed files.
; userData, photo libraries, original images and external outputs are untouched.
