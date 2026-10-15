; Inno Setup 6 script of the SPC installer. Build with build.ps1 (it passes AppVersion and SourceDir).
; The program goes to Program Files; the data (database, settings, certificates, backups, qualification records) to ProgramData\SPC and are never removed by the uninstaller.
; Order of the steps: stop the server and back up an existing database -> copy the files -> first setup (folders, database, administrator, settings, certificate)
;   -> installation qualification (IQ) -> optional operational qualification (OQ) -> register the server task and start it. A failed qualification leaves the server stopped.
;
; Unattended installation (IT deployment, the Windows test of the build):
;   SPC-Setup-x.y.z.exe /VERYSILENT /SUPPRESSMSGBOXES /LOG="setup.log" /DATADIR="D:\SPC" /HOST=127.0.0.1 /PORT=8000 /ADMIN=admin /ADMINPWFILE="pw.txt" [/TLS] [/FIREWALL] [/NOOQ]
;   /ADMINPWFILE names a file whose first line is the password of the first administrator (needed only when the data folder has no database yet); the installer
;   copies it and does not delete the given file: delete it yourself. Each step writes "SPC: ..." into the setup log, and the last line is "SPC: result ok" or "SPC: result failed ...".

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\windows\app"
#endif

[Setup]
AppId={{6F1B7D5A-3C0E-4F2B-9D5B-5A7C3E8D1A42}
AppName=SPC
AppVersion={#AppVersion}
AppPublisher=SPC
DefaultDirName={autopf}\SPC
DefaultGroupName=SPC
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=SPC-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=SPC {#AppVersion}
CloseApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
#ifexist "compiler:Languages\ChineseTraditional.isl"
Name: "zhtw"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
#endif

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\SPC (open in browser)"; Filename: "{code:BrowserUrl}"
Name: "{group}\Qualification records"; Filename: "{code:DataDir}\qualification"

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\unregister-task.ps1"""; Flags: runhidden; RunOnceId: "UnregisterTask"

[Code]
var
  DataPage, NetPage, AdminPage: TInputQueryWizardPage;
  DataDirEdit: TNewEdit;
  TlsCheck, OqCheck, FirewallCheck: TNewCheckBox;
  ExistingDb: Boolean;

function DataDir(Param: String): String;
begin
  Result := DataPage.Values[0];
end;

function BrowserUrl(Param: String): String;
var
  Scheme: String;
begin
  if TlsCheck.Checked then Scheme := 'https' else Scheme := 'http';
  Result := Scheme + '://localhost:' + NetPage.Values[1] + '/';
end;

procedure InitializeWizard;
begin
  DataPage := CreateInputQueryPage(wpSelectDir, 'Data folder', 'Where are the data kept?',
    'The database, settings, certificates, backups and qualification records are kept here. The uninstaller does not remove them.');
  DataPage.Add('Data folder:', False);
  DataPage.Values[0] := ExpandConstant('{param:DATADIR|}');
  if DataPage.Values[0] = '' then DataPage.Values[0] := ExpandConstant('{commonappdata}\SPC');

  NetPage := CreateInputQueryPage(DataPage.ID, 'Network', 'Who can reach the server?',
    'Use 127.0.0.1 for this computer only. Use 0.0.0.0 to serve the network; then use https.');
  NetPage.Add('Listen on address:', False);
  NetPage.Add('Port:', False);
  NetPage.Values[0] := ExpandConstant('{param:HOST|127.0.0.1}');
  NetPage.Values[1] := ExpandConstant('{param:PORT|8000}');
  TlsCheck := TNewCheckBox.Create(NetPage);
  TlsCheck.Parent := NetPage.Surface;
  TlsCheck.Caption := 'Serve https with a self-signed certificate (browsers warn until it is trusted)';
  TlsCheck.Checked := WizardSilent and (Pos('/TLS', UpperCase(GetCmdTail)) > 0);
  TlsCheck.Left := 0; TlsCheck.Top := NetPage.Edits[1].Top + NetPage.Edits[1].Height + ScaleY(14); TlsCheck.Width := NetPage.SurfaceWidth;
  FirewallCheck := TNewCheckBox.Create(NetPage);
  FirewallCheck.Parent := NetPage.Surface;
  FirewallCheck.Caption := 'Open the port in the Windows firewall';
  FirewallCheck.Checked := WizardSilent and (Pos('/FIREWALL', UpperCase(GetCmdTail)) > 0);
  FirewallCheck.Left := 0; FirewallCheck.Top := TlsCheck.Top + ScaleY(24); FirewallCheck.Width := NetPage.SurfaceWidth;

  AdminPage := CreateInputQueryPage(NetPage.ID, 'First administrator', 'Who administers SPC?',
    'Used only when the data folder has no users yet. The password needs at least the length the program asks for.');
  AdminPage.Add('Administrator name:', False);
  AdminPage.Add('Password:', True);
  AdminPage.Add('Repeat the password:', True);
  AdminPage.Values[0] := ExpandConstant('{param:ADMIN|admin}');
  OqCheck := TNewCheckBox.Create(AdminPage);
  OqCheck.Parent := AdminPage.Surface;
  OqCheck.Caption := 'Run the operational qualification (OQ) after the installation (about one minute)';
  OqCheck.Checked := Pos('/NOOQ', UpperCase(GetCmdTail)) = 0;
  OqCheck.Left := 0; OqCheck.Top := AdminPage.Edits[2].Top + AdminPage.Edits[2].Height + ScaleY(14); OqCheck.Width := AdminPage.SurfaceWidth;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // an existing installation keeps its settings (address, port, certificate): spc-setup does not overwrite them, so the page would change nothing
  Result := (PageID = NetPage.ID) and FileExists(DataPage.Values[0] + '\spc.json');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Port: Integer;
begin
  Result := True;
  if CurPageID = NetPage.ID then begin
    Port := StrToIntDef(NetPage.Values[1], 0);
    if (Port < 1) or (Port > 65535) then begin
      MsgBox('The port must be a number from 1 to 65535.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = AdminPage.ID then begin
    if not FileExists(DataPage.Values[0] + '\spc.sqlite3') then begin
      if (Trim(AdminPage.Values[0]) = '') or (AdminPage.Values[1] = '') then begin
        MsgBox('Give the name and the password of the first administrator.', mbError, MB_OK);
        Result := False;
      end else if AdminPage.Values[1] <> AdminPage.Values[2] then begin
        MsgBox('The two passwords differ.', mbError, MB_OK);
        Result := False;
      end;
    end;
  end;
end;

procedure Report(const Text: String);
begin
  Log('SPC: ' + Text);
end;

// the password of the first administrator: typed on the page, or the first line of the file given with /ADMINPWFILE (unattended installation)
function AdminPassword(): String;
var
  F: String;
  Lines: TArrayOfString;
begin
  Result := AdminPage.Values[1];
  F := ExpandConstant('{param:ADMINPWFILE|}');
  if (Result = '') and (F <> '') and LoadStringsFromFile(F, Lines) and (GetArrayLength(Lines) > 0) then Result := Lines[0];
end;

function Run(const Exe, Params: String; var Code: Integer): Boolean;
begin
  Result := Exec(Exe, Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Py, Data, Pw, Args, Log: String;
begin
  Data := DataPage.Values[0];
  Pw := '';
  Py := ExpandConstant('{app}\python\python.exe');
  if CurStep = ssInstall then begin
    // an upgrade: stop the server and make a checked copy of the database before any file is replaced
    ExistingDb := FileExists(Data + '\spc.sqlite3');
    if ExistingDb then begin
      Exec('schtasks.exe', '/End /TN "SPC Server"', '', SW_HIDE, ewWaitUntilTerminated, Code);
      if FileExists(Py) then begin
        if not Run(Py, '-m spc.admin --db "' + Data + '\spc.sqlite3" backup --to "' + Data + '\backup"', Code) then Code := -1;
        Report('backup before the upgrade, code ' + IntToStr(Code));
        if Code <> 0 then
          if MsgBox('The backup of the existing database failed. Continue without it?', mbConfirmation, MB_YESNO) = IDNO then
            RaiseException('Installation stopped: the backup failed.');
      end;
    end;
  end;
  if CurStep = ssPostInstall then begin
    // first setup: folders, database, administrator, settings, certificate
    Args := '-m spc.setup --data-dir "' + Data + '" --host ' + NetPage.Values[0] + ' --port ' + NetPage.Values[1];
    if TlsCheck.Checked then Args := Args + ' --tls self-signed';
    if not ExistingDb then begin
      if AdminPassword() = '' then begin
        Report('result failed: no password for the first administrator (give /ADMINPWFILE)');
        MsgBox('No password for the first administrator. The server was not started.', mbError, MB_OK);
        Exit;
      end;
      Pw := ExpandConstant('{tmp}\spc-first-password.txt');
      SaveStringToFile(Pw, AdminPassword() + #13#10, False);
      Args := Args + ' --admin "' + Trim(AdminPage.Values[0]) + '" --password-file "' + Pw + '"';
    end;
    if not Run(Py, Args, Code) then Code := -1;
    if Pw <> '' then DeleteFile(Pw);  // the password never stays on the disk, also when the setup failed
    Report('first setup, code ' + IntToStr(Code));
    if Code <> 0 then begin
      Report('result failed: first setup');
      MsgBox('The first setup failed (code ' + IntToStr(Code) + '). The server was not started.', mbError, MB_OK);
      Exit;
    end;
    // installation qualification (and operational qualification when asked); a failed check keeps the server stopped
    Log := Data + '\qualification';
    if OqCheck.Checked then Args := '-m spc.qualification all --quiet --out "' + Log + '" --db "' + Data + '\spc.sqlite3" --require-manifest'
    else Args := '-m spc.qualification iq --out "' + Log + '" --db "' + Data + '\spc.sqlite3" --require-manifest';
    if not Run(Py, Args, Code) then Code := -1;
    Report('qualification, code ' + IntToStr(Code));
    if Code <> 0 then begin
      Report('result failed: qualification');
      MsgBox('The qualification found a failed check. The server was not started. Read the record in ' + Log, mbError, MB_OK);
      Exit;
    end;
    Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\register-task.ps1') + '" -AppDir "' + ExpandConstant('{app}') + '" -DataDir "' + Data + '" -Port ' + NetPage.Values[1];
    if FirewallCheck.Checked then Args := Args + ' -OpenFirewall';
    if not Run('powershell.exe', Args, Code) then Code := -1;
    Report('server task, code ' + IntToStr(Code));
    if Code <> 0 then begin
      Report('result failed: server task');
      MsgBox('The server task could not be registered (code ' + IntToStr(Code) + ').', mbError, MB_OK);
      Exit;
    end;
    Exec('schtasks.exe', '/Run /TN "SPC Server"', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Report('result ok');
  end;
end;
