# 競品比較

最後更新：2026-11-04。本文件只做紀錄，不代表要做功能變動。缺少的功能是否加入，另行決定。

## 1. 本軟體的定位

- **對象**：所有製造業。只要是製造業，就需要 SPC 管理。不限汽車業。
  - 離散製造：機械加工、電子組裝、射出成型、沖壓、金屬加工。
  - 流程製造：化工、食品、塑膠、紙業、塗料。
  - 高科技：半導體、面板、PCB、被動元件。
  - 醫療器材、製藥、航太。
- **方法依據**：
  - ISO 7870（管制圖）、ISO 22514（製程能力）、ISO/TR 11462。
  - AIAG-VDA SPC 手冊 2026（目前依 2026 年 2 月草案）、AIAG MSA、VDA 5。
  - 這些標準的統計方法適用所有產業。汽車業的 IATF 16949 只是其中一個使用情境。
- **原則**：已有的功能先用；比較後發現某產業缺少的功能，才考慮加入。

## 2. LineWorks SPACE（camLine，現屬 Elisa IndustrIQ）

### 2.1 資料來源與可信度

- 來源只有廠商產品頁、展覽資料與比價網站（見第 5 節）。沒有技術文件，也沒有試用。
- 廠商官網在撰寫時連不上，內容來自搜尋結果的摘要。
- 架構、資料庫、效能數字都是廠商宣稱，沒有驗證。
- 使用者曾在工廠擔任製程工程師時實際使用過 LineWorks SPACE。實際使用經驗比本節的網頁資料更可靠，可補充到第 2.4 節。

### 2.2 產品概要

- 網頁式 SPC 系統，屬 camLine LineWorks MES 套件的品質模組。
- 主要客戶：半導體與高科技、多廠區企業。
- 宣稱可同時評估數千張管制圖，並以中央資料庫保存所有資料。
- 部署：雲端、SaaS，或自建 Windows／Linux 伺服器（依比價網站）。
- 語言：中、英、德、日（依比價網站）。
- 價格：比價網站列起價約 €60,000，依功能計價。需向廠商確認。

### 2.3 功能對照

| 項目 | LineWorks SPACE（廠商宣稱） | 本軟體（現況） |
|---|---|---|
| 判異規則 | 宣稱 89 種以上，含 Western Electric；可依「通道」設定 | 管制界限、連串、趨勢、中間三分之一、3 點中 2 點超過 2σ、5 點中 4 點超過 1σ、連續 15 點在 1σ 內。每條規則逐條開啟 |
| 管制圖種類 | 沒查到完整清單 | 計量、計數、驗收圖、預控制圖、CUSUM、EWMA、Z-MR、T²、MEWMA、MCUSUM、延伸界限、Pearson、自迴歸殘差、多串流、Laney p′/u′ 等 |
| 異常通知 | 製造電腦、Email、簡訊 | 監控畫面、導覽列的警報數字、webhook（JSON）。沒有內建 Email 或簡訊 |
| 異常處理 | 疑難排解指引（TSG）、矯正措施（CA）；eCAP 外掛自動處理矯正措施 | 失控反應計畫（OCAP）、事件流程（重測、調整、圍堵、根本原因、升級、結案）、首件與組長確認 |
| 管制界限管理 | 試算（pre-run）、版本管理、稽核軌跡（離線模組）；STARGATE 管理多廠的圖與規則 | 界限有版次與理由、歷史保留；新界限需工程師設定 |
| 報表 | 每日／週／月／季自動定期報表；網頁圖表與 Email 圖表 | 20+2 元素報告（HTML、Excel、JSON 歸檔）、持續績效報告。沒有自動定期報表 |
| 匯出 | Excel、qs-STAT | CSV、Excel、JSON 歸檔；讀取 qs-STAT 的 DFD/DFQ 描述資料（不讀數值） |
| 資料來源 | 機台、MES、其他生產系統；openAPI REST 服務 | 手動輸入、CSV／Excel 匯入、OPC UA、REST API |
| 儀表板 | 排序、篩選、下鑽、柏拉圖分析 | 站別總覽、監控清單、未結案事件。沒有柏拉圖與跨線下鑽 |
| 登入 | OIDC 單一登入，連線加密 | 本機帳號（5 種角色）、https。沒有 AD／LDAP／OIDC |
| 多廠區 | 支援（中央資料庫、STARGATE） | 一個工廠一台伺服器 |
| 規模 | 宣稱數千張圖同時評估 | SQLite 單檔資料庫，沒做過負載測試 |
| 缺點資料 | SDC 外掛（獨立缺點資料庫） | 計數管制圖（p、np、c、u）；沒有缺點分類資料庫 |
| 量測系統分析 | 沒查到 | 量具 R&R、Type 1、穩定性、計數一致性、ISO 22514-7、MSA 閘門 |
| 軟體確效 | 沒查到 | 內建查證與確效（含 ISO/TR 11462-3 參考資料）、IQ/OQ、發行清單雜湊 |
| 控制計畫、角色能力 | 沒查到 | 控制計畫（核准與發行）、SPC 角色能力矩陣 |
| 電子簽章 | 沒查到 | 報告歸檔的外部簽章（Ed25519、ECDSA、RSA），稽核雜湊鏈 |
| 語言 | 中、英、德、日 | 繁體中文、英文 |
| 部署 | 雲端／SaaS／自建 | 自建 Windows（安裝程式）、Linux（發行套件）；兩者都有自動測試 |

### 2.4 使用者實際經驗（待補）

請補充在工廠使用 LineWorks SPACE 的經驗，例如：

- 最常用的畫面與功能。
- 覺得好用的地方。
- 覺得不好用、操作麻煩的地方。
- 工程師、操作員、主管各自怎麼用。

## 3. 對「所有製造業」而言可能缺少的功能

以下只是紀錄。是否加入，依客戶需求另行決定。

| 缺少的功能 | 哪些產業特別需要 | 備註 |
|---|---|---|
| Email／簡訊通知 | 所有產業，特別是夜班人力少的工廠 | 可用現有 webhook 轉接 |
| 自動定期報表（日／週／月） | 所有產業 | LineWorks 有 |
| 跨產線儀表板、柏拉圖、下鑽 | 多產線、多機台工廠；半導體 | LineWorks 有 |
| 缺點分類與柏拉圖 | 電子組裝、射出、外觀檢驗 | 目前只有計數管制圖 |
| Nelson 規則中的「14 點交替上下」與「8 點在 1σ 外（兩側）」 | 習慣使用 Nelson 8 條規則的產業 | 其他 6 條已有 |
| 管制界限試算與版本比較 | 所有產業 | 目前有版次與歷史，沒有試算 |
| AD／LDAP／OIDC 單一登入 | 中大型企業 | |
| 多廠區、中央資料庫 | 集團、多廠區企業 | 目前一廠一台伺服器 |
| 大量資料與高頻資料的效能 | 半導體、流程製造（每秒取樣） | SQLite，沒做負載測試 |
| 量具直接輸入（RS-232、USB 卡尺、測微計） | 機械加工、離散製造 | 目前有 OPC UA，沒有量具介面 |
| 批次與配方製程的管制（批次間、配方別） | 化工、食品、製藥 | Z-MR 可處理多產品短流程；沒有配方管理 |
| 法規電子紀錄（例如 FDA 21 CFR Part 11） | 製藥、醫療器材、食品 | 已有稽核雜湊鏈與外部簽章，但沒有依 Part 11 驗證 |
| 其他語言（簡體中文、日文、德文、越南文等） | 海外工廠、外籍員工 | 目前只有繁中與英文 |
| 雲端版 | 小型工廠、沒有 IT 人員的工廠 | 目前只有自建伺服器 |

## 4. 本軟體相對的強項

- 統計方法依國際標準，並有內建查證與確效（含 ISO/TR 11462-3 的 11 組參考資料）。
- 判異規則逐條開啟，避免規則太多造成誤報。
- 量測系統分析（MSA）與管制圖、報告、控制計畫連動：量測系統不合格時，監控與報告會被擋下。
- 內建 IQ/OQ 與發行雜湊，方便稽核。
- 安裝簡單：Windows 安裝程式、Linux 發行套件，不需要另外的資料庫伺服器。

## 5. 資料來源

- [LineWorks SPACE（camLine 產品頁）](https://www.camline.com/products/lineworks/lineworks-space)
- [LineWorks SPACE（Elisa IndustrIQ 中文頁）](https://www.elisaindustriq.com/zh-tw/camline/products/lineworks-space)
- [LineWorks SPACE Brochure](https://www.elisaindustriq.com/camline/products/lineworks-space-brochure)
- [LineWorks SPACE eCAP](https://www.camline.com/products/lineworks/lineworks-space-ecap)
- [LineWorks STARGATE](https://www.camline.com/products/lineworks/lineworks-stargate)
- [Advanced SPC（camline）](https://www.elisaindustriq.com/camline/solutions/advanced-statistical-process-control-spc)
- [SEMICON West：LineWorks SPACE](https://semicon-west.german-pavilion.com/en/sites/products-services/193754)
- [camLine GmbH（Silicon Saxony）](https://silicon-saxony.de/en/mitglieder/camline-gmbh-2/)
- [camLine COMPAMED 2025 資料](https://www.medica.de/vis-content/event-compamed2025/exh-compamed2025.3049867/COMPAMED-2025-camLine-GmbH-Paper-compamed2025.3049867-BfsA86imTKmjyJaPV2efaQ.pdf)
- [GetApp：LineWorks SPACE](https://www.getapp.com/spc-software/a/lineworks-space)
- [Capterra：LineWorks SPACE](https://www.capterra.ca/software/148828/lineworks-space)
