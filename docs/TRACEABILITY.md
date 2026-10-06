# 草案逐條對照表

對象：AIAG-VDA SPC Manual Yellow Volume，VDA-QMC 2026-02-01 版（`docs/AIAG-VDA-SPC-Yellow-Volume.pdf`）。
依據：草案**原文**（第 5～13 章逐段讀過），不是依據先前的摘要文章。第 1～4、14 章是前言、範圍、標準清單、名詞與參考文獻，沒有軟體要求，不列。
本表由 `tests/test_traceability.py` 檢查：表中引用的每個檔案與每個測試都必須存在，所以不會指向已被改名或刪掉的東西。

**狀態**

| 符號 | 意義 |
|---|---|
| ✅ | 已實作，有測試（或有對標準數值的驗證） |
| 🔶 | 已實作，但草案沒有給公式或規則，**是我們的解讀**（需要懂統計的人審閱） |
| ◐ | 部分實作，缺的部分寫在備註 |
| ⬜ | 未實作 |
| ➖ | 說明性文字或組織責任，軟體沒有對應功能 |

統計：✅ 63，🔶 6，◐ 20，⬜ 5，➖ 6（共 100 列）

「驗證」欄的測試以「tests/檔名::測試名」表示。另有獨立證據：ISO/TR 11462-3 的資料集（內建確效報告，`src/spc/validation/iso11462.py`）、ISO 22514-8 附錄的算例（`src/spc/validation/iso22514.py`）、草案自己的數值範例（表 8-1、9-3、12 章範例）、AIAG MSA 手冊例。

---

## 第 5 章　SPC 管理與控制迴路

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 5.1 | 預防優於檢出；零缺陷策略 | ➖ | — | — | 方法論說明 |
| 5.2 | PDCA 改善循環 | ◐ | `src/spc/monitor/service.py`（警報 → 反應 → 驗證 → 結案的步驟） | `tests/test_monitor.py::test_the_action_plan_must_be_acknowledged_by_each_person_and_again_after_a_change` | 反應流程對應 PDCA 的 Do／Check／Act；Plan 與整體改善循環是組織的事 |
| 5.3 | 製程控制系統：對製程採取行動，對輸出的行動只是暫時措施 | ✅ | `src/spc/monitor/service.py`（事件步驟含調整參數、調整要素、根因、遏制措施） | `tests/test_monitor.py::test_an_incident_that_cannot_be_fixed_goes_to_root_cause_and_containment` | |
| 5.4 | 迴路 1：現場 SPC，反應觸發與反應 | ✅ | `src/spc/monitor/`、`src/spc/api/monitors.py`、介面「監控」 | `tests/test_monitor.py::test_a_violation_opens_one_incident_with_the_instructions_of_the_plan` | |
| 5.4 | 迴路 2：合格閘門（通過合格、攔下不合格） | ◐ | 容許界限圖與預控圖（`src/spc/core/charts/`、`src/spc/monitor/model.py`）；MSA 閘門擋監控 | `tests/test_monitor.py::test_the_acceptance_chart_finds_the_accepted_fraction_out_of_tolerance_with_the_stated_probability` | 沒有批次放行（合格／不合格處置）的功能 |
| 5.4 | 迴路 3：製程後改善，定期評估穩定性與能力 | ✅ | `src/spc/service/analysis.py`、持續績效報告（`src/spc/monitor/service.py`） | `tests/test_monitor.py::test_ongoing_report_gives_indices_quadrant_trend_limits_review_and_response` | |
| 5.4 | 迴路 4～6：產品、製程、系統稽核 | ➖ | 稽核鏈只提供證據（`src/spc/auth/audit.py`） | `tests/test_auth.py::test_the_audit_chain_shows_a_change` | 沒有稽核（audit）管理功能 |

## 第 6 章　SPC 應用的要求

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 6.1 | 十項前提（規格、量測、製程特性、規劃、OCAP、抽樣計畫、選圖、控制計畫、資料收集、角色、定期稽核） | ◐ | 見 6.2～6.8 各列 | — | 製程特性（6.4）與定期稽核（迴路 4～6）沒有功能；抽樣計畫與選圖見下 |
| 6.2.1 | 公差與責任原則；量測不確定度與生產變異不重複計入；公差限＝功能限 | ➖ | — | — | 設計責任 |
| 6.2.1、圖 6-2 | 擴充不確定度 U_MP 與驗收界限、防護帶 | ◐ | `src/spc/core/msa.py::expanded_uncertainty`；報告元素 22 | `tests/test_report.py::test_guard_band_follows_the_draft_example`、`tests/test_msa.py::test_the_expanded_uncertainty_and_the_guard_band_follow_the_report_of_the_draft` | 防護帶 g 算出並寫入報告（草案第 12 章範例 U＝0.0211、g＝0.0175 吻合）；驗收界限 L_A、U_A 不作為圖或判定使用；U 是簡化合成（見 6.3） |
| 6.2.2 | 風險分析（FMEA）決定哪些特性做 SPC | ➖ | 控制計畫的特性分類（`src/spc/plan/`） | — | FMEA 本身不在軟體內 |
| 6.2.3 | 特殊特性；能力不足時改 100 % 檢驗 | ◐ | 控制計畫每行有分類與管制方式（SPC 圖／抽樣／100 % 檢驗／其他）`src/spc/plan/model.py` | `tests/test_plan.py::test_a_line_needs_what_the_draft_lists` | 不會因為能力不足自動建議 100 % 檢驗 |
| 6.3 | 有效且有能力的量測與檢驗過程（依 MSA／VDA 5）；IATF 要求計量型**與計數型**都要驗證 | ◐ | `src/spc/core/msa.py`（量具 R&R、Type 1、穩定性、擴充不確定度）、`src/spc/msa/gate.py`（閘門） | `tests/test_msa.py::test_the_gauge_rr_of_the_aiag_example`、`tests/test_msa.py::test_the_gate_is_open_with_proof_and_blocked_without_it` | **計數型量測系統分析（Kappa 等）未做**；線性與偏差研究、巢狀 GRR、完整 ISO 14253-1 預算未做；Type 1 公式為 VDA 5 常用算法，未對照 ISO 22514-7（只有預覽的第 1～4 章） |
| 6.4 | 製程特性化（DoE／迴歸找出控制因子） | ⬜ | — | — | 沒有 DoE 或迴歸功能 |
| 6.5 | 生產與檢驗規劃 | ◐ | 控制計畫（量測系統、方法、樣本數與頻率）`src/spc/plan/` | `tests/test_plan.py::test_the_measurement_system_and_the_room_for_its_uncertainty` | 規劃的其餘面向（工作站、物流）不在軟體內 |
| 6.6 | OCAP：調整矩陣、負責人與升級、製程日誌、確保人員理解 | ✅ | `src/spc/monitor/model.py`（每條規則的反應計畫、升級）；事件紀錄；每人確認 | `tests/test_monitor.py::test_the_action_plan_must_be_acknowledged_by_each_person_and_again_after_a_change`、`tests/test_monitor.py::test_an_overdue_incident_is_flagged_and_the_response_times_are_reported` | |
| 6.7 | 控制計畫（核准、版本、含量測系統與反應計畫） | ✅ | `src/spc/plan/`、`src/spc/api/plans.py` | `tests/test_plan.py::test_the_life_of_a_control_plan`、`tests/test_plan.py::test_a_report_carries_the_released_plan_into_its_archive` | |
| 6.8.1 | 七個 SPC 角色與職責 | ✅ | `src/spc/plan/roles.py`、介面「角色」 | `tests/test_plan.py::test_the_matrix_roles_and_competences_of_people` | |
| 6.8.2、表 6-1 | 角色 × 能力矩陣 | ✅ | `src/spc/plan/roles.py` | `tests/test_plan.py::test_the_role_matrix_is_table_6_1_of_the_draft` | 逐格核對 |

## 第 7 章　方法總覽

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 7.2、表 7-1 | P 與 C 的使用條件：穩定性未證明用 P，證明穩定才用 C | ✅ | `src/spc/core/capability/naming.py` | `tests/test_capability.py::test_naming_gate` | |
| 7.2、表 7-2 | 三種研究（機台、前期、製程）的指標與變異來源 | ✅ | `src/spc/service/analysis.py`（階段 `machine`／`preliminary`／`production`） | `tests/test_service.py::test_machine_study_uses_individuals_and_pm_names` | |
| 7.2 | 「受控但非統計穩定」（in control）可用 Cp、Cpk | ✅ | `src/spc/service/analysis.py`（`controlled_stable`） | `tests/test_rules_and_stability.py::test_classify_stability` | 由使用者宣告，須有證據；時間模型 A1、A2 才算統計受控 |
| 7.3 | 機台績效：固定其他 4M，50～100 件連續生產，記錄條件，定性趨勢評估 | ✅ | `src/spc/study/checklist.py`（23 項清單） | `tests/test_study.py::test_roles_and_the_whole_life_of_a_study` | |
| 7.4 | 製程能力：125 件（25 組 × 5）；少於時調整目標；前期績效須標示 | ✅ | `src/spc/core/capability/target.py`、`src/spc/core/capability/naming.py` | `tests/test_targets.py::test_preliminary_targets_match_table_9_3` | |
| 7.5 | 管制圖種類（個別值、平均、中位數、全距、標準差）；分析圖與 SPC 圖；過程導向與公差導向 | ✅ | `src/spc/core/charts/variable.py`、`src/spc/service/analysis.py`、`src/spc/monitor/` | `tests/test_variable_charts.py::test_xbar_s_limits_follow_formulas` | |
| 7.5 | 判異準則：連串 ≥ 7、趨勢 ≥ 7、中間三分之一（25 點中少於 11 或多於 23）、WE／Nelson | ✅ | `src/spc/core/rules.py` | `tests/test_rules_and_stability.py::test_middle_third_rule_flags_too_few_points_in_the_middle`、`tests/test_rules_and_stability.py::test_two_of_three_beyond_2_sigma` | |
| 7.5、10.2.4 | OC 曲線與 ARL | ✅ | `src/spc/core/arl_oc.py`、介面「工具」 | `tests/test_arl_oc_and_params.py::test_arl_table` | |
| 7.6 | 離群值：以理由標為無效、不刪除、不納入計算；離群檢定只是提示 | ✅ | `src/spc/data/dataset.py`（標記）、`src/spc/data/outliers.py`（提示） | `tests/test_data_dataset.py::test_marking_needs_reason_and_person`、`tests/test_data_outliers.py::test_hints_never_change_the_dataset` | |
| 7.7、表 7-4 | 各研究的樣本策略與目標值（Pm≥2.00 等，範例） | ✅ | `src/spc/core/capability/target.py` | `tests/test_targets.py::test_machine_targets_match_table_8_1` | 目標值可依客戶改（`src/spc/profile.py`） |
| 7.8.1 | 選擇分布：依製程知識，須檢驗，不可只靠檢定；形位公差的理論分布是折疊分布；可用 Box-Cox、Johnson 轉換 | ◐ | `src/spc/core/distributions.py`（常態、對數常態、韋伯、伽瑪、Johnson SU、Box-Cox、混合、經驗；另有 weibull2、rayleigh 須明示選用） | `tests/test_distributions.py::test_automatic_choice_follows_the_data` | **折疊常態分布未提供**（草案點名為形位公差的理論分布）；AIC 與 Anderson-Darling 只描述配適，不是檢定 |
| 7.8.2.1 | General Geometric（.G）：X0.135 %、X50 %、X99.865 % 分位數；經驗分位數須 ≥ 2000 筆 | ✅ | `src/spc/core/capability/indices.py::geometric_indices` | `tests/test_capability.py::test_geometric_equals_classic_formulas_for_normal`、`tests/test_distributions.py::test_empirical_g_needs_a_big_sample_and_works_with_one` | |
| 7.8.2.2 | 單邊公差：只算 Pmk／Ppk／Cpk；有自然界限時加算 Pm／Pp／Cp 但不設目標 | ✅ | `src/spc/core/capability/indices.py` | `tests/test_capability.py::test_one_sided_specification_gives_only_the_location_index`、`tests/test_report.py::test_one_sided_specification` | |
| 7.8.2.3 | 超出比例法（.Z）：由尾部機率換算 z | ✅ | `src/spc/core/capability/indices.py::zscore_indices` | `tests/test_capability.py::test_zscore_equals_geometric_for_normal`、`tests/test_distributions.py::test_z_score_keeps_the_resolution_far_in_the_tail` | |
| 7.8.2.4 | 兩種方法的比較 | ✅ | 分析請求的 `method`（G／Z），報告寫明方法 | `tests/test_distributions.py::test_g_and_z_differ_for_a_skewed_distribution` | |
| 7.8.2.5 | 組內能力 Cw／Cwk：輔助分析工具，**不得用於報告** | ✅ | `src/spc/core/capability/indices.py::within_indices`；報告刻意不含 | `tests/test_report.py::test_within_subgroup_indices_are_never_in_the_report`、`tests/test_xlsx.py::test_no_within_subgroup_index_anywhere` | |

## 第 8 章　機台績效

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 8.1 | 非機台的 4M 與機台參數須保持不變；變動須記錄；偏離須與客戶協議 | ✅ | `src/spc/study/checklist.py`（人工確認項，協議須附備註） | `tests/test_study.py::test_manual_items_need_a_note_where_the_draft_asks_for_an_agreement` | |
| 8.2.1 | 樣本 50 件；較少須客戶核准且目標值提高；刀具磨耗大須涵蓋 ≥ 1.5 個修整週期 | ✅ | `src/spc/study/checklist.py` | `tests/test_study.py::test_sample_size_needs_50_parts_or_an_approval_of_the_customer`、`tests/test_study.py::test_a_process_with_high_tool_wear_must_cover_one_and_a_half_dressing_cycles` | |
| 8.2.2 | 材料均質、符合規格 | ✅ | `src/spc/study/checklist.py`（人工確認） | `tests/test_study.py::test_roles_and_the_whole_life_of_a_study` | |
| 8.2.3 | 每個量測過程有 MSA／VDA 5 的能力證明 | ✅ | `src/spc/msa/gate.py`；研究項目 `msa_evidence` 由閘門自動評估 | `tests/test_msa.py::test_the_machine_study_item_msa_evidence_comes_from_the_gate` | 受 6.3 的缺口限制 |
| 8.2.4 | 暖機、刀具已用過、不中斷、調整到公差中心 | ◐ | `src/spc/study/checklist.py` | `tests/test_study.py::test_the_machine_is_adjusted_close_to_the_middle_of_the_tolerance` | **「刀具不可調或壽命 ≤ 5 件時，經客戶同意可不評 Pmk」未做** |
| 8.2.5 | 預生產（選用）：1 件在公差中心 ±12.5 %；5 件平均同上且全距 < 25 %；單邊自然界限 62.5 % | ✅ | `src/spc/study/checklist.py` | `tests/test_study.py::test_pre_production_run_of_one_part_two_sided`、`tests/test_study.py::test_pre_production_run_of_five_parts_checks_the_mean_and_the_range`、`tests/test_study.py::test_pre_production_run_with_a_natural_limit_uses_62_5_percent_of_the_tolerance` | |
| 8.2.6 | 多夾具、多模穴：每個工位當成獨立機器；模穴之間與之內另行檢驗 | ◐ | 研究記錄的「工位／夾治具／模穴」欄位（每個一份研究） | `tests/test_study.py::test_roles_and_the_whole_life_of_a_study` | **模穴之間／之內的分析未做**（只提示） |
| 8.3.1 | 資料可追溯：生產與收集順序、時間線 | ✅ | `src/spc/data/dataset.py`（來源列、時間戳記、標記日誌） | `tests/test_data_dataset.py::test_subgroups_matrix_and_traceability` | |
| 8.3.2 | 試件保留並封鎖至驗收 | ✅ | `src/spc/study/checklist.py`（人工確認） | `tests/test_study.py::test_roles_and_the_whole_life_of_a_study` | 軟體只記錄確認，不管理實物 |
| 8.3.3 | 數值記錄，保留有效位數 | ✅ | `src/spc/data/csv_io.py`、`src/spc/data/dataset.py` | `tests/test_study.py::test_traceability_and_numeric_data_and_the_distribution_come_from_the_data_set` | |
| 8.3.4.1 | 定性穩定性評估（值曲線，離群與無原因的型態） | ✅ | 人工確認項；報告含值曲線（元素 12） | `tests/test_report.py::test_machine_study_has_no_control_chart_and_no_stability_claim` | |
| 8.3.4.2 | 依 7.8.1 評估分布 | ◐ | 見 7.8.1 | — | 同 7.8.1 的缺口 |
| 8.4、表 8-1 | 目標值隨樣本數調整（信賴水準 99.99 %）；Critical 在 n < 50 為 n.a. | ✅ | `src/spc/core/capability/target.py` | `tests/test_targets.py::test_machine_targets_match_table_8_1`、`tests/test_targets.py::test_critical_machine_study_cannot_use_a_reduced_sample` | 逐格核對 |
| 8.5.1 | 多段加工：組合數、每組合 ≥ 5 件、每工序 ≥ 50 件、相同組合可省略；合併評估、找出偏離的組合；形位類 50 件平均分配主軸；只量部分治具的潛力 | 🔶 | `src/spc/core/multistage.py`、`src/spc/service/special.py`、介面「特殊情形」 | `tests/test_special.py::test_scope_reproduces_the_draft_example_2`、`tests/test_special.py::test_a_deviating_pallet_is_found_and_the_numbers_match_independent_formulas` | 草案沒有指定判定偏離的檢定（用 Welch、變異數分析、Brown-Forsythe 作輔助）；「只量部分治具」的件數是我們的解讀 |
| 8.5.2 | 多維特性 Pm、Pmk：超橢球、機率 p、u_p／3 | 🔶 | `src/spc/core/multivariate_perf.py` | `tests/test_special.py::test_the_one_dimensional_case_gives_the_univariate_indices`、`tests/test_special.py::test_distance_to_the_tolerance_border_matches_a_brute_force_search` | 指標取雙側分位數 Φ⁻¹((1+p)/2)，是我們的解讀（一維等於一般 Pm、Pmk）；ISO 22514-6 未取得，未對照 |
| 8.5.3 | GD&T 與 MMC／LMC：組裝間隙 C、C50 %、C0.135 %、C99.865 %，對 L_C = 0 的指標 | 🔶 | `src/spc/core/gdt.py` | `tests/test_special.py::test_clearance_is_zero_exactly_at_the_bonus_boundary`、`tests/test_special.py::test_the_sign_printed_in_the_draft_would_reward_a_position_error` | **草案孔的式子印成 C＝+xD＋xP−MMVS，與其自己的極限例矛盾，程式採負號並標明**；互換性要求與多特徵未做；ISO 22514-6、-9 未取得 |

## 第 9 章　製程績效與能力

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 9.2 | 隨機抽樣，例如 25 組 × 5 件，須代表工具、批次、班別 | ◐ | `src/spc/service/analysis.py`（子群結構、不完整子群處理） | `tests/test_service.py::test_marked_values_leave_the_calculation_and_their_subgroup_is_reported` | 樣本是否代表製程由人判斷；程式只處理子群結構 |
| 9.3 | 製程分析前須證明量測能力並已分析機台績效；不中斷、變更須記錄 | ◐ | MSA 閘門（量測）；研究清單 | `tests/test_msa.py::test_a_report_is_refused_for_a_blocked_system_and_carries_the_uncertainty_of_a_proven_one` | 報告**不檢查**是否已有結案的機台績效研究（也寫在 `docs/ARCHITECTURE.md` 10.3） |
| 9.4、表 9-1、9-2 | 八種時間相依分布模型 A1、A2、B、C1～C4、D；只有 A1、A2 統計受控 | ✅ | `src/spc/core/time_model.py`、`src/spc/service/model_suggestion.py`、分析請求的 `model` | `tests/test_time_model.py::test_the_evidence_and_the_reasons_follow_the_decision_of_the_draft_table`、`tests/test_time_model.py::test_each_model_is_found_in_most_simulated_processes` | 草案沒有給判定程序：自動建議是 🔶（標準檢定，只是建議，使用者決定）；模擬判對率 65～97 % |
| 9.4 | 配適品質：先看全部資料，再看最靠近所算指標那一側規格限的 25 % 資料；機率圖、直方圖、淨相關係數 | ◐ | 機率圖與直方圖（`src/spc/report/svg.py`）；Anderson-Darling 與 AIC | `tests/test_distributions.py::test_candidates_report_failures_instead_of_hiding_them` | **25 % 尾端配適評估與淨相關係數未做** |
| 9.4、表 10-2 | 依時間模型選分析圖與 SPC 圖、樣本大小與頻率 | ⬜ | 圖種都有（Shewhart、Pearson、延伸界限、驗收圖） | — | **沒有「依模型推薦圖種與抽樣」的功能**，由使用者選圖 |
| 9.5、表 9-3 | 前期績效與製程績效／能力的目標值（隨樣本數調整） | ✅ | `src/spc/core/capability/target.py` | `tests/test_targets.py::test_preliminary_targets_match_table_9_3` | 逐格核對 |

## 第 10 章　管制圖與持續能力

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 10.1 | 過程導向與公差導向兩種管制概念；改善後須重新計算界限 | ✅ | `src/spc/monitor/service.py`（界限版次，改界限須附理由）、驗收圖 | `tests/test_monitor.py::test_the_limits_stay_fixed_until_somebody_sets_new_ones_with_a_reason` | |
| 10.2.1、表 10-1 | 分析圖（回溯，迴路 3）與 SPC 圖（現場，迴路 1）；SPC 圖每次違規須有行動 | ✅ | `src/spc/service/analysis.py`；`src/spc/monitor/` | `tests/test_monitor.py::test_limits_from_reference_data_equal_the_analysis_chart` | |
| 10.2.2 | 穩定性準則；SPC 圖不要無差別疊加準則 | ✅ | `src/spc/core/rules.py`（逐項啟用）、`src/spc/core/stability.py` | `tests/test_rules_and_stability.py::test_default_rule_set_checks_limits_only` | 預設只啟用界限 |
| 10.2.2.2、10.3.2.3 | 分析圖：警報數超過預期的誤警數才算不穩定；k ≥ 300 時可忽略誤警的隨機範圍 | ✅ | `src/spc/core/stability.py`（`LARGE_K = 300`） | `tests/test_rules_and_stability.py::test_default_mode_follows_the_draft_and_tolerates_chance_alarms` | |
| 10.2.3 | 不穩定時的矯正：重量、調整製程參數、調整要素、根因分析、遏制；改變後重新評估界限 | ✅ | `src/spc/monitor/service.py` | `tests/test_monitor.py::test_the_plan_is_followed_step_by_step_and_an_incident_closes_only_on_proof` | |
| 10.2.4 | 管制圖效能：OC、ARL | ✅ | `src/spc/core/arl_oc.py` | `tests/test_arl_oc_and_params.py::test_oc_values_for_a_one_sigma_shift` | |
| 10.3.1 | 圖面要素：標頭資料、中心線、界限、警告界限、樣本編號與時間／使用者、違規標記、事件與對策記錄；現場圖不顯示規格限 | ✅ | `src/spc/monitor/`（圖表資料不含規格；規格只用於持續績效報告）、`src/spc/web/static/app.js` | `tests/test_monitor.py::test_check_point_flags_limits_warnings_and_only_what_the_new_point_completes` | |
| 10.3.2、圖 10-5 | 選圖指南（資料類型、觀察方向、概念、有無記憶、非常態、前期、多變量、短批） | ⬜ | 各種圖都有，見下列各列 | — | **沒有互動式選圖指南**；選擇靠使用者與文件 |
| 10.3.2.6 | 非常態：轉換（Box-Cox、Johnson）、自迴歸模型殘差圖；殘差圖的修正要反轉換 | ◐ | `src/spc/core/charts/dependent.py`（AR 殘差圖）；分布轉換在 `distributions.py` | `tests/test_monitor.py::test_an_ar_monitor_end_to_end` | 草案沒有給 AR 的公式（🔶 標準方法）；殘差圖的修正量**不會自動反轉換**回原單位 |
| 10.3.2.7 | 前期管制：用預期的最大變異或相近製程的參數；預先驗收圖；預控圖；只用於監視不用於控制 | ✅ | `src/spc/monitor/model.py`（參數來源）；預控圖 | `tests/test_monitor.py::test_pre_control_zones_follow_the_classical_rules` | 預控圖草案沒有規則，用古典規則（標明） |
| 10.3.2.8 | 多變量：Hotelling T²、MEWMA、MCUSUM | ✅ | `src/spc/core/charts/multivariate.py` | `tests/test_monitor.py::test_hotelling_limit_has_the_stated_false_alarm_rate`、`tests/test_monitor.py::test_the_mcusum_limit_agrees_with_crosier_and_the_recursion_is_the_published_one` | 草案沒有給公式，用標準式（文件已註明）🔶 |
| 10.3.2.9 | 短批：Z-MR；穩定化計數型圖處理不同樣本大小 | ◐ | `src/spc/core/charts/` Z-MR；計數型圖的界限逐點隨樣本大小 | `tests/test_monitor.py::test_the_zmr_monitor_standardises_each_product_and_keeps_one_set_of_limits` | 標準化的計數型圖（z 化的 p、u）未做 |
| 10.3.3.2 | X̄-s 圖：σ̂＝√(s̄²)；變異圖用 χ² 精確界限 | ✅ | `src/spc/core/charts/variable.py::xbar_s` | `tests/test_variable_charts.py::test_xbar_s_limits_follow_formulas`、`tests/test_variable_charts.py::test_false_alarm_rate_matches_alpha` | 草案說先檢查 s 圖再評估 X̄ 圖：兩張圖同時評估，**沒有強制順序** |
| 10.3.3.3 | X̄-R 圖：w 分布精確界限，d2、d3 表 | ✅ | `src/spc/core/charts/variable.py::xbar_r`、`src/spc/core/constants.py` | `tests/test_constants.py::test_w_quantiles_match_draft`、`tests/test_variable_charts.py::test_range_chart_false_alarm_rate_matches_alpha` | 草案表逐格核對 |
| 10.3.3.4 | 中位數-全距圖：c_n 係數 | ✅ | `src/spc/core/charts/variable.py::median_r` | `tests/test_monitor.py::test_the_median_chart_limits_follow_the_c_n_factor_and_the_stated_risk` | |
| 10.3.3.5 | I-MR 圖 | ✅ | `src/spc/core/charts/variable.py::imr`（含重啟與階段） | `tests/test_variable_charts.py::test_imr_estimators`、`tests/test_imr_restart.py::test_a_moving_range_never_spans_a_restart_and_sigma_comes_from_inside_the_segments` | |
| 10.3.4 | 驗收圖：k_A＝u(1−p)＋u(P_A)/√n；σ̂ ≤ T/10；p＝1 %、P_A＝99 % | ✅ | `src/spc/monitor/model.py` | `tests/test_monitor.py::test_the_acceptance_chart_finds_the_accepted_fraction_out_of_tolerance_with_the_stated_probability`、`tests/test_monitor.py::test_acceptance_limits_refuse_a_variation_that_is_too_large_and_flag_one_above_a_tenth` | |
| 10.3.5.2 | Pearson 管制圖 | ✅ | `src/spc/core/pearson.py` | `tests/test_monitor.py::test_the_pearson_chart_limits_are_the_tail_quantiles_of_the_skewed_distribution` | 只有監控；分析圖沒有 Pearson 版本 |
| 10.3.5.3 | 延伸界限 Shewhart 圖：σ_out 由 ANOVA 或最大／最小 3 個平均估計，u_out＝1.5 | ✅ | `src/spc/core/charts/extended.py` | `tests/test_monitor.py::test_extended_limits_allow_for_the_moving_mean_and_equal_a_plain_chart_without_it` | 只有監控；分析圖沒有延伸界限版本 |
| 10.3.5.4 | CUSUM（表格式，h、k、起始頭獎） | ✅ | `src/spc/core/charts/sequential.py` | `tests/test_monitor.py::test_the_cusum_arl_reproduces_the_table_of_the_draft` | 草案 ARL 表重現 |
| 10.3.5.5 | EWMA | ✅ | `src/spc/core/charts/sequential.py` | `tests/test_monitor.py::test_ewma_limits_at_the_start_are_narrower_so_a_shift_at_the_start_is_found_sooner` | |
| 10.3.6.2～10.3.6.5 | 計數型圖 p、np、u、c | ✅ | `src/spc/core/charts/attribute.py`（精確二項／卜瓦松界限） | `tests/test_attribute_charts.py::test_np_chart_limits_hold_the_stated_risk`、`tests/test_attribute_charts.py::test_p_chart_variable_sample_size_gives_point_by_point_limits` | |
| 10.4 | 持續績效與能力報告：滾動期間重算指標、四象限（圖 10-26）、界限檢討（太窄／太寬）、反應時效 | ✅ | `src/spc/monitor/service.py`（持續績效報告） | `tests/test_monitor.py::test_ongoing_report_gives_indices_quadrant_trend_limits_review_and_response`、`tests/test_monitor.py::test_the_limits_review_notices_limits_that_are_too_narrow_too_wide_or_off_centre` | |
| 10.4 | 依時間模型的風險分組、樣本大小與頻率建議；理性抽樣與理性分組 | ⬜ | — | — | **沒有抽樣大小與頻率的建議功能**（ARL 與 OC 工具可用來自行評估） |
| 10.3.1、10.3.2 | 多串流與巢狀資料的變異來源（SoV）研究 | 🔶 | `src/spc/core/nested.py`、介面「特殊情形」 | `tests/test_special.py::test_balanced_nested_anova_equals_the_textbook_formulas`、`tests/test_special.py::test_unbalanced_estimators_are_unbiased_for_the_known_components` | 草案只點名沒有給方法；動差法巢狀變異數分析；交叉因子與 REML 未做 |
| 10.3.2.6、9.4 C3 | 有趨勢的製程（刀具磨耗） | 🔶 | `src/spc/core/charts/trend.py`（迴歸管制圖，分析用） | `tests/test_special.py::test_trend_chart_matches_an_independent_regression` | 草案沒有給公式；不是監控；有趨勢的製程只算績效指標 |
| 10.5 | 其他方法：PAA、AI 異常偵測；內部可另用，替代協議標準時須經雙方同意 | ➖ | — | — | 草案只舉例，未要求 |

## 第 11 章　軟體的應用

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 11.1 | 自動監控、通知偏差；即時視覺化與歷史資料；自訂警報條件；事件、行動、原因須記錄 | ✅ | `src/spc/monitor/`、`src/spc/monitor/notify.py`（Webhook） | `tests/test_monitor.py::test_notifications_go_out_once_per_incident_and_a_broken_receiver_changes_nothing`、`tests/test_monitor.py::test_the_webhook_notifier_posts_json_and_survives_a_dead_url` | |
| 11.1 | 量測資料由設備經介面自動傳入資料庫（例：OPC UA）；介面須驗證 | ✅ | `src/spc/equipment/`（OPC UA） | `tests/test_equipment.py::test_a_link_is_tested_before_it_is_enabled_and_a_change_starts_again`、`tests/test_equipment.py::test_the_test_read_and_the_runner_against_a_real_server` | 以本機測試伺服器驗證；**加密連線只接好線，未測；未接過真實設備** |
| 11.1 | 標準化介面格式（舉例 ISO/TR 11462-5） | ⬜ | — | — | 未做；本標準本文未取得 |
| 11.1 | 與 CAQ 系統整合 | ◐ | REST API、CSV 與 Excel 匯入匯出 | `tests/test_api.py::test_export_keeps_marks_and_opens_in_excel` | 沒有特定 CAQ 系統的連接器 |
| 11.2 | 分析軟體須驗證（verification）與確認（validation）；以測試範例比對文件化的參考結果 | ✅ | `src/spc/validation/`（內建確效報告，約 500 項檢查）、`src/spc/validation/iso11462.py` | `tests/test_validation.py::test_the_eleven_iso_examples_are_run_against_the_program_and_nothing_is_hidden`、`tests/test_validation.py::test_a_wrong_program_fails_the_standards_examples` | ISO/TR 11462-3 的 11 個範例；標準自己前後不一致處標為「已知」並附計算證據 |
| 11.2 | 參數須透明（最小樣本、離群處理、信賴區間、估計式、單邊公差），否則等於黑盒子 | ✅ | `src/spc/params.py`（參數與指紋，隨結果與封存保存） | `tests/test_arl_oc_and_params.py::test_params_are_explicit_and_hashable_for_archiving` | |
| 11.2 | 客戶特定參數集可設定並保存 | ✅ | `src/spc/profile.py`、介面「管理 → 客戶設定檔」 | `tests/test_profiles.py::test_resolve_takes_the_profile_for_unset_fields_and_names_deviations`、`tests/test_profiles.py::test_the_archive_keeps_a_snapshot_so_a_later_change_does_not_touch_the_report` | |
| 11.2 | 用於證明量測過程能力的分析軟體也須驗證、確認、「有能力」（參 6.6） | ◐ | 內建確效涵蓋 SPC 計算；量具 R&R 以 AIAG 例核對 | `tests/test_msa.py::test_the_gauge_rr_of_the_aiag_example` | MSA 部分只有 AIAG 一個手冊例，沒有 ISO 22514-7 的範例 |

## 第 12 章　文件與報告

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 12.1 | 報告內容：製程識別、規格、收集條件、量測過程、樣本資訊、圖形、分布、穩定性、樣本統計、指標、解讀 | ✅ | `src/spc/report/builder.py`、`src/spc/report/render.py` | `tests/test_report.py::test_numbers_in_the_report_equal_the_analysis` | |
| 12.2 | 報告元素 1～20（必要）與 21、22（選用） | ✅ | `src/spc/report/render.py`、`src/spc/report/xlsx.py` | `tests/test_report.py::test_all_22_elements_are_present`、`tests/test_xlsx.py::test_elements_sheet_lists_the_20_plus_2_elements` | |
| 12.2 | 範例報告（圖 12-1～12-3）；格式須由客戶與供應商協議 | 🔶 | 客戶設定檔的版面（抬頭、欄位、語言、目標表）`src/spc/profile.py` | `tests/test_profiles.py::test_report_has_the_layout_of_the_customer` | 版面依元素清單，不是逐像素複製草案的圖；草案範例的數值（Cpk.G、防護帶）有核對 |
| 12.2 | 報告以可選多語言呈現 | ✅ | `src/spc/report/texts.py`（繁中與英文） | `tests/test_report.py::test_report_texts_have_the_same_keys_and_placeholders` | |

## 第 13 章　可追溯性與歸檔

| 節 | 草案要求（意旨） | 狀態 | 程式 | 驗證 | 備註 |
|---|---|---|---|---|---|
| 13 | 文件須防竄改、受保護、可取得、可讀；可追溯到原因事件；已處理資料須連同評估與所用參數一起保存 | ✅ | `src/spc/report/archive.py`（SHA-256 摘要、資料＋參數＋結果）、`src/spc/auth/audit.py`（雜湊鏈）、`src/spc/signing/`（外部簽章） | `tests/test_report.py::test_any_change_to_the_archive_is_noticed`、`tests/test_report.py::test_reproduction_finds_a_result_that_does_not_follow_from_the_data`、`tests/test_signing.py::test_a_changed_archive_invalidates_the_signature` | 封存檔可離線重算驗證；簽章不含時間戳記服務、不驗憑證鏈 |
| 13 | 檢驗指令、量測與檢驗方法須文件化、版本化、歸檔 | ◐ | 控制計畫（版本、核准、快照）`src/spc/plan/`；量測系統記錄 | `tests/test_plan.py::test_the_life_of_a_control_plan` | 檢驗指令書本身不是軟體的文件類型 |
| 13 | 保存期限、儲存媒體、儲存地點由公司決定；文件分類系統 | ➖ | — | — | 組織決策；軟體**沒有保存期限或自動清除**功能，資料集可被其擁有者或管理員刪除（報告保留當時的快照） |
| 13 | 可追溯到起因事件與製程（CQI-28、IATF 7.5.3.2.1） | ◐ | 稽核鏈、標記與重啟日誌、事件紀錄 | `tests/test_auth.py::test_the_audit_chain_shows_a_change`、`tests/test_auth.py::test_removing_the_newest_entries_is_only_seen_against_a_hash_kept_elsewhere` | 只提供證據鏈；刪除最新幾筆稽核紀錄只有拿另外保存的雜湊才看得出（已在程式與文件說明） |

---

## 缺口總表（⬜ 與 ◐，依對稽核的影響排序）

1. **計數型量測系統分析**（6.3，IATF 要求計量與計數都驗證）：未做。
2. **ISO 22514-7 的計算部分**：未做，等決定與標準全文；Type 1 的標示已改為不主張依據該標準。
3. **依時間模型推薦圖種與抽樣大小／頻率**（表 10-2、10.4）與**選圖指南**（圖 10-5）：未做。
4. **折疊分布**（7.8.1）與 **25 % 尾端配適評估**（9.4）：未做。
5. **8.2.4 的 Pmk 免評條款**（刀具不可調或壽命 ≤ 5 件，經客戶同意）：未做。
6. **8.2.6 模穴之間／之內分析**：未做。
7. **製程特性化（DoE／迴歸）**（6.4）：未做。
8. **標準化介面格式**（ISO/TR 11462-5）：未做。
9. 報告不檢查機台績效研究是否已結案（9.3）。
10. 標準化的計數型圖（10.3.2.9）、AR 殘差圖的反轉換（10.3.2.6）。

## 我方解讀清單（🔶，建議請統計人員審閱）

| 項目 | 草案怎麼說 | 我們怎麼做 |
|---|---|---|
| 多維 Pm、Pmk 的 u_p | 「p 分位數／99.865 % 分位數（＝3）」 | 取雙側分位數 Φ⁻¹((1+p)/2)；一維時恰為一般 Pm、Pmk |
| GD&T 孔的間隙式 | C＝+xD＋xP−MMVS | 取 C＝xD−xP−MMVS（草案自己的極限例要求負號） |
| 多段加工偏離的判定 | 只說用平均與變異、或管制圖找出 | Welch t（Bonferroni）、ANOVA、Brown-Forsythe 作輔助，不自動排除資料 |
| 只量部分治具 | 其餘每個治具與夾位 5 件 | 其餘治具數 × 每治具工件數 × 5 |
| 時間模型自動建議 | 「常可由製程性質推論」，沒有程序 | 依表 9-1 的順序做標準檢定，只給建議 |
| 巢狀變異來源 | 只點名 | 動差法巢狀變異數分析 |
| 趨勢圖 | 只說明製程 | 迴歸管制圖（每週期一條線），指標只算績效 |
| 多變量、AR、預控圖 | 只點名或只給古典規則 | 標準的 T²／MEWMA／MCUSUM、AR 殘差圖、古典預控規則 |
| 量測不確定度 | 草案第 12 章只給結果 | U＝k√(σ_GRR²＋(偏差/√3)²＋(解析度/√12)²＋u_cal²)，吻合草案範例 |
| MSA 判定門檻 | 草案沒有給 | VDA 5／AIAG 常用值（≤10 %、≤30 %、ndc≥5、Cg≥1.33），屬系統政策可改 |

## 超出草案、為 IATF 稽核與軟體管制而加的功能

| 功能 | 說明 | 位置 |
|---|---|---|
| 登入、角色、稽核雜湊鏈 | 變更可追溯，防竄改 | `src/spc/auth/` |
| 外部簽章 | 報告封存的摘要由程式外的金鑰簽署，程式只驗證；受信任簽署者名單 | `src/spc/signing/` |
| 內建確效報告 | 程式自證：ISO/TR 11462-3、ISO 22514-8 範例與草案數值 | `src/spc/validation/` |

---

## 草案引用的 IATF 16949 條款

| 條款 | 草案的說法 | 本軟體的對應 |
|---|---|---|
| 7.1.5.2.1 | 生產相關軟體須驗證；驗證與確認都是必要 | 第 11.2 節各列：內建確效報告、參數透明、客戶參數集 |
| 7.5.3.2.1 | 記錄保存 | 第 13 章各列；保存期限由公司決定，軟體不強制 |
| 8.3.3.3 | 特殊特性 | 控制計畫的分類（6.2.3） |

本表不判斷新版 IATF 16949 的條文（尚未取得），只列草案自己引用的條款。
