# 安裝、確認（IQ/OQ）、備份與升級

本文件給兩種人：**現場工程師**（把軟體裝到客戶那裡）和**建置者**（做出安裝程式）。

## 1. 誰負責什麼

| 項目 | 供應商（我們） | 客戶 |
|---|---|---|
| 軟體驗證證據（內建確效報告、追溯表） | 提供 | 審閱、歸檔 |
| 安裝確認 IQ、運作確認 OQ | 工具與記錄格式、安裝時執行 | 簽名、歸檔 |
| 績效確認 PQ（用客戶自己的零件、量具、界限） | 不在本軟體範圍 | 客戶自己證明 |
| 資料交換格式、歷史資料匯入 | 通用入口與匯入範本；現場對應欄位 | 提供檔案與規則 |
| 主機、網路、防火牆、備份排程、時間同步 | 建議 | 負責 |

軟體驗證要「安裝的版本就是驗證過的版本」：發行版有 **RELEASE.json**（每個檔案的 SHA-256 與鎖定的套件版本），IQ 逐一比對。

## 2. 系統需求

- **作業系統**：Windows 10／11 或 Windows Server 2016 以上，64 位元（安裝程式針對此環境）。Linux 也能跑，見第 9 節。
- **記憶體**：4 GB 以上。**磁碟**：程式約 400 MB，另加資料與備份（至少留 1 GB 空間，IQ 會檢查）。
- **使用者端**：Edge、Chrome 或 Firefox 最新版，不必安裝任何東西。
- **Python**：**不需要另外安裝**，安裝程式內含。
- **網路**：預設只接受本機（127.0.0.1）。要讓區網使用，安裝時選 0.0.0.0，並**使用 https**。
- **時間**：稽核紀錄與報告用 UTC 時間；請讓電腦與公司時間來源同步。

## 3. 安裝（Windows）

1. 以**系統管理員**執行 `SPC-Setup-<版本>.exe`。
2. 依畫面：安裝資料夾 → **資料資料夾**（預設 `C:\ProgramData\SPC`，資料庫、設定、憑證、備份、確認記錄都在這裡，解除安裝**不會**刪除）→ 網路（位址、連接埠、是否 https、是否開防火牆）→ 第一位管理員與密碼（資料夾內已有使用者時不需要）。
3. 安裝程式依序做：（升級時先停止伺服器並備份資料庫）→ 複製檔案 → 首次設定 → **IQ** →（勾選時）**OQ** → 註冊伺服器工作並啟動。
4. **IQ 或 OQ 有任何一項失敗，伺服器不會啟動**；記錄在 `資料資料夾\qualification\`，先看記錄再處理。
5. 開啟瀏覽器：`http(s)://<電腦名稱>:<連接埠>/`，用第一位管理員登入。

伺服器是一個**排程工作**「SPC Server」：開機自動啟動、以 SYSTEM 執行、停止後自動重啟（最多 5 次，間隔 1 分鐘）。資料資料夾的權限限制為系統管理員與 SYSTEM。

自簽憑證：瀏覽器會警告，直到把憑證（`資料資料夾\certs\spc.crt`）加入受信任。有公司憑證時，改用 `spc-setup --tls files --cert … --key … --force-config`。

## 4. 確認記錄 IQ／OQ

指令（命令提示字元，在安裝資料夾的 `bin` 內）：

```
spc-qualify iq  --out D:\記錄 --db C:\ProgramData\SPC\spc.sqlite3 --require-manifest
spc-qualify oq  --out D:\記錄
spc-qualify all --out D:\記錄 --db … --require-manifest
spc-qualify verify D:\記錄\IQOQ-….json
```

每次產生：JSON 記錄、可列印的 HTML（英文與繁體中文，附簽名欄）、內建驗證報告（OQ）。記錄有 SHA-256 摘要；`verify` 找出被改過的記錄。退出碼：0 無失敗、1 有失敗、2 用法錯誤。

**IQ 檢查**（只看，不改站台資料）：Python 版本與位元數；必要套件版本；**RELEASE.json 完整**、程式版本相同、**每個已安裝檔案與建置時相同**（有被改、缺少或多出的檔案即失敗）、套件版本與發行時測試的相同；網頁介面檔案；資料庫完整性、結構版本、稽核鏈（唯讀開啟）；資料資料夾可寫入；可用磁碟空間；時鐘與時區。

**OQ 檢查**（在暫存資料庫上啟動真正的伺服器，用 HTTP 操作，答案由 numpy 另外算）：網頁與安全標頭；未登入被拒；錯誤密碼被拒；三種角色登入；檢視者不能匯入、工程師不能讀稽核紀錄；匯入 125 筆；平均、標準差、Pp、Ppk 與獨立計算相同（1e-9）；相同輸入得相同結果；報告與其封存檢查、被改過的封存會被發現；**內建驗證報告約 900 項檢查無失敗**；稽核鏈完整；備份與還原；資料庫重開後資料仍在。

**不在記錄內**：績效確認 PQ。客戶要用自己的零件、量具與界限證明軟體適用於其製程。

**記錄怎麼用**：列印或另存 PDF，執行、審閱、核准人簽名後歸檔；把記錄摘要寫進交貨單（「這個版本的 RELEASE.json 摘要」也在建置輸出）。摘要不是簽章：要防止有權限的人同時改檔案與摘要，請把摘要用別的管道交給客戶。

## 4.1 畫面時間與時區

所有時間（樣本、事件、稽核鏈、報告封存、匯出）一律以 **UTC** 儲存，稽核雜湊與報告摘要都以 UTC 計算。畫面上的時間則以**廠區時區**顯示，並一律附上偏移量，例如「2026-10-19 10:10:39 UTC+8」；頂列會顯示「時間：Asia/Taipei（UTC+8）」。

- 廠區時區是 `spc.json` 的 `display_timezone`（IANA 名稱，例如 `Asia/Taipei`）。首次設定時預設取這台電腦的時區，取不到就用 UTC；安裝時可用 `/TIMEZONE=Asia/Taipei` 指定，`spc-setup` 用 `--display-timezone`。
- 時區由伺服器統一決定，不看各台瀏覽器的設定，所以同一筆記錄在每台電腦上顯示的時間都相同。
- 監控頁「取樣時間」欄輸入的時間，也以廠區時區解讀，再換算成 UTC 儲存。
- 修改時區：編輯 `spc.json` 的 `display_timezone` 後重新啟動伺服器。無效的名稱會讓伺服器拒絕啟動並說明原因；變更會寫入稽核鏈（`display_timezone_set`，記錄從哪個時區改成哪個）。
- IQ 會記錄廠區時區與電腦時區，兩者不同時加以註明。伺服器電腦請用 NTP 對時。
- 報告（HTML、Excel）與確認記錄上的時間保持 ISO 8601 的 UTC 格式（結尾的 `Z` 代表 UTC）。

## 5. 升級

重新執行新版安裝程式，選**同一個資料資料夾**。安裝程式會先停止伺服器並做**有檢查的備份**（`資料資料夾\backup\`），再複製新檔案；資料庫在伺服器啟動時自動遷移到新結構。升級時已有 `spc.json` 的話，網路設定頁會略過、沿用原設定；首次設定用的暫存密碼檔在設定結束後立即刪除。升級後 IQ／OQ 會再跑一次，新記錄與舊記錄並存。備份失敗時會問是否繼續，**不建議繼續**。

## 6. 備份與還原

```
spc-admin --db C:\ProgramData\SPC\spc.sqlite3 backup --to D:\SPC備份
spc-admin --db C:\ProgramData\SPC\spc.sqlite3 restore D:\SPC備份\spc-backup-….sqlite3
```

備份可在伺服器運行時做（SQLite 線上備份），會做完整性與稽核鏈檢查，旁邊有 `.sha256` 檔，事件寫入稽核鏈。**還原前請先停止伺服器**（`Stop-ScheduledTask -TaskName "SPC Server"`）：伺服器運行時持有 `<資料庫>.server.lock`，`restore` 會拒絕（`server_running`），同一個資料庫也不能啟動第二個伺服器。備份的來源路徑不存在時會報錯（不會產生空的資料庫），來源未通過完整性檢查時拒絕備份；備份不遷移舊結構的資料庫。還原檢查備份未損壞、稽核鏈完整、結構版本不比程式新、SHA-256 相符；被取代的資料庫會保留為 `…before-restore-<時間>`。

建議用 Windows 工作排程器每天執行 `backup`，並把備份複製到另一台機器。**備份放在同一顆磁碟不算備份。** 另請定期把稽核鏈最後的雜湊值（介面「管理」）存到資料庫以外的地方，才能發現整條鏈被重寫。

## 7. 解除安裝

「設定 → 應用程式」解除安裝：移除伺服器工作與防火牆規則，**保留資料資料夾**。要刪除資料，人工刪除該資料夾（先確認有備份與保存期限的決定）。

## 8. 給建置者：做出安裝程式

在 **64 位元 Windows** 上，裝好：與 `-PythonVersion` 同一個小版本的 Python（例如 3.11，`py -3.11`）、Inno Setup 6、可連到 python.org 與 PyPI。

```
cd packaging\windows
.\build.ps1 -PythonVersion 3.11.9 -PythonZipSha256 <python-3.11.9-embed-amd64.zip 的 SHA-256>
```

鎖定檔由 `tools/lock_windows.py` 產生（用 Windows 的環境條件計算依賴），只有刻意更新套件版本時才重跑；重跑後要跑測試再提交，新的版本就是一次發行內容的變更。Windows 自動測試也用同一份鎖定檔安裝，所以測試的套件版本就是安裝程式裡的版本。

建置步驟：下載內嵌 Python 並**核對 SHA-256**（不符即停止）→ 從**鎖定檔**安裝套件（`packaging\windows\requirements-web-sign.lock`，`-WithOpcUa` 時用 `requirements-web-sign-opcua.lock`：每個套件的確切版本與 wheel 的 SHA-256，`pip --require-hashes`，任何不同的檔案或鎖定檔沒有列的依賴都會停止建置），再裝程式本身 → 寫入 **RELEASE.json** → 對建置結果跑 **IQ/OQ 自我測試**（失敗即停止）→ 打包安裝程式。輸出在 `dist\`：`SPC-Setup-<版本>.exe`、一份文字檔（安裝程式的 SHA-256 與發行清單摘要，給交貨單）、`build-qualification-<版本>\`（建置時的確認記錄）。

> **狀態：** 建置、無人值守安裝、IQ/OQ、伺服器鎖、備份還原、升級與解除安裝已在 GitHub 的 Windows Server 虛擬機上自動執行並通過（見下方「Windows 自動測試」）。**圖形精靈、重開機、https 與防火牆、Windows 11 實機尚未驗證**，交付前請在實機逐項確認：
> 1. `build.ps1` 能跑完且自我測試通過。
> 2. 在乾淨的 Windows 虛擬機安裝：頁面、IQ、OQ 都通過，工作「SPC Server」在執行，瀏覽器可登入。
> 3. 重新開機後伺服器自動啟動。
> 4. 升級：先裝舊版、建一些資料，再裝新版，資料仍在、備份存在、新的 IQ/OQ 通過。
> 5. 解除安裝後資料資料夾仍在、工作已移除。
> 6. https 自簽憑證與防火牆選項。
> 發現的問題先修再交付客戶。

**Windows 自動測試（GitHub Actions，`.github/workflows/windows.yml`）**：每次推送在 GitHub 的 Windows Server 虛擬機上：跑完整測試；用 `build.ps1` 建置安裝程式（含建置時的 IQ/OQ）；無人值守安裝；檢查伺服器回應、管理員登入、IQ/OQ 記錄通過且未被更改；伺服器運行中還原被拒、同一資料庫無法啟動第二個伺服器、停止後可還原；再安裝一次（升級路徑：先備份、資料保留、開始功能表仍指向原連接埠）；解除安裝後資料仍在、工作已移除；缺少管理員密碼的安裝以結束代碼 11 結束且不啟動伺服器。安裝檔、安裝記錄與確認記錄保存為該次執行的 artifact。上面第 3 項（重開機）、第 6 項（https 與防火牆）與實際操作畫面仍需人工在實機確認。內嵌 Python 的 SHA-256 請設為 repository variable `PYTHON_EMBED_SHA256`（取 python.org 公布的值）；未設定時測試流程採用下載當下的值並發出警告，該次產物不可作為正式版。

## 9. Linux

沒有安裝程式；用 pip 安裝後同一組指令：

```
python3 -m venv /opt/spc && /opt/spc/bin/pip install "spc[web,sign]"
/opt/spc/bin/spc-setup --data-dir /var/lib/spc --admin admin --tls self-signed --host 0.0.0.0 --port 8443
/opt/spc/bin/spc-qualify all --out /var/lib/spc/qualification --db /var/lib/spc/spc.sqlite3
/opt/spc/bin/spc-serve --config /var/lib/spc/spc.json
```

以 systemd 常駐時，`ExecStart` 用最後一行，`Restart=always`。（Linux 的步驟已在開發環境測過指令本身，未做完整的 systemd 部署測試。）

## 10. 已知限制

- 伺服器是排程工作，不是 Windows 服務：功能足夠（開機啟動、自動重啟），但不出現在「服務」清單。
- 安裝程式的介面是英文（Inno Setup 預設）；文件與軟體介面有繁體中文。
- 無人值守安裝：`SPC-Setup-<版本>.exe /VERYSILENT /SUPPRESSMSGBOXES /LOG="setup.log" /DATADIR="D:\SPC" /PORT=8000 /ADMIN=admin /ADMINPWFILE="pw.txt" [/HOST=0.0.0.0] [/TIMEZONE=Asia/Taipei] [/TLS] [/FIREWALL] [/NOOQ]`。密碼檔第一行是第一位管理員的密碼（只有資料夾裡還沒有資料庫時需要），安裝程式不會刪除你給的檔案，請自行刪除。記錄檔中每個步驟有一行 `SPC: …`，最後一行是 `SPC: result ok` 或 `SPC: result failed …`。**結束代碼**：0 = 已安裝、確認通過並啟動；11 = 沒有第一位管理員的密碼；12 = 首次設定失敗；13 = IQ/OQ 未通過；14 = 伺服器工作無法註冊（後四種情況伺服器不會啟動）；1–8 是 Inno Setup 本身的代碼（安裝沒有執行或被中止）。每個步驟的完整輸出寫在 `<資料資料夾>\logs\install.log`。升級時開始功能表的「SPC (open in browser)」依 `spc.json` 的連接埠與 https 設定建立。
- 沒有沒有自動更新、沒有授權機制、安裝程式**沒有程式碼簽章**（Windows SmartScreen 會警告「未知的發行者」，需要簽章憑證才能消除）。
- 單機 SQLite：適合單一廠區、數十位同時使用者；多廠區或大量連續寫入時需要 PostgreSQL（架構已預留，尚未實作）。
- IQ 的「套件版本鎖定」比對的是建置時安裝的所有套件版本；若客戶端另外裝了套件，只會標示差異，不會失敗。
