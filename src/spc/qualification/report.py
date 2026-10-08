"""The qualification record: a JSON record with a SHA-256 digest over its content, and the same as a printable HTML page with the places for the signatures.

The record is what the customer files. `verify_record` recomputes the digest, so a changed record is found. The digest is not a signature: it shows that the record is not changed by accident or
afterwards by someone who does not also recompute it. For more, sign the file outside the program, or write the digest into the delivery note.
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone

import spc

STATUS_ORDER = ("fail", "warn", "info", "pass")

TEXT = {
    "en": {
        "title": {"iq": "Installation qualification (IQ)", "oq": "Operational qualification (OQ)", "iqoq": "Installation and operational qualification (IQ/OQ)"},
        "intro": "This record shows that the program installed on this computer is the released program and that it works as specified. Performance qualification (PQ) with the customer's own parts, gauges and limits is the customer's own proof and is not part of this record.",
        "release": "Release", "version": "Program version", "manifest": "Release manifest digest", "host": "Computer", "user": "Run by (operating system user)", "platform": "System", "python": "Python",
        "started": "Started (UTC)", "verdict": "Result", "verdict_pass": "PASSED", "verdict_warn": "PASSED, with items for a person to review", "verdict_fail": "FAILED",
        "summary": "{p} passed, {f} failed, {w} to review, {i} for information", "check": "Check", "expected": "Expected", "actual": "Found", "status": "Status", "note": "Note",
        "st_pass": "pass", "st_fail": "FAIL", "st_warn": "review", "st_info": "info", "digest": "Digest of this record (SHA-256)",
        "verify": "To check that this file was not changed: spc-qualify verify FILE.json",
        "sign": "Signatures", "executed": "Executed by", "reviewed": "Reviewed by", "approved": "Approved by", "name": "Name", "date": "Date", "signature": "Signature",
        "attached": "Attached records", "none": "none",
    },
    "zh-TW": {
        "title": {"iq": "安裝確認（IQ）", "oq": "運作確認（OQ）", "iqoq": "安裝與運作確認（IQ/OQ）"},
        "intro": "本記錄證明安裝在這台電腦上的程式就是發行的程式，且依規格運作。用客戶自己的零件、量具與界限做的績效確認（PQ）是客戶自己的證明，不在本記錄內。",
        "release": "發行版", "version": "程式版本", "manifest": "發行清單摘要", "host": "電腦", "user": "執行者（作業系統帳號）", "platform": "系統", "python": "Python",
        "started": "開始時間（UTC）", "verdict": "結果", "verdict_pass": "通過", "verdict_warn": "通過，另有項目需人員審閱", "verdict_fail": "未通過",
        "summary": "通過 {p} 項，失敗 {f} 項，待審閱 {w} 項，資訊 {i} 項", "check": "檢查項目", "expected": "預期", "actual": "實際", "status": "狀態", "note": "備註",
        "st_pass": "通過", "st_fail": "失敗", "st_warn": "審閱", "st_info": "資訊", "digest": "本記錄的摘要（SHA-256）",
        "verify": "要確認這個檔案沒有被改過：spc-qualify verify 檔名.json",
        "sign": "簽名", "executed": "執行", "reviewed": "審閱", "approved": "核准", "name": "姓名", "date": "日期", "signature": "簽名",
        "attached": "附帶記錄", "none": "無",
    },
}

CHECK_TITLES = {
    "en": {
        "python_version": "Python version", "python_bits": "Python word size", "manifest": "Release manifest", "manifest_digest": "Release manifest is intact", "program_version": "Program version equals the release",
        "files_intact": "Installed files are the files of the build", "packages_locked": "Packages are the versions the release was tested with", "web_files": "Web interface files",
        "database": "Database file", "database_integrity": "Database integrity check", "database_schema": "Database schema version", "database_audit_chain": "Audit chain of the database",
        "database_permissions": "Database file permissions", "database_create": "A new database can be created", "data_folder_writable": "Data folder is writable", "disk_free": "Free disk space",
        "clock": "Clock and time zone", "oq_page": "Web page and security headers", "oq_requires_login": "Login is required", "oq_wrong_password": "Wrong password refused", "oq_login": "Users sign in",
        "oq_role_viewer": "Role: viewer cannot import", "oq_role_audit": "Role: engineer cannot read the audit trail", "oq_import": "Data import", "oq_index_mean": "Analysis: mean", "oq_index_sd": "Analysis: standard deviation",
        "oq_index_p": "Analysis: Pp", "oq_index_pk": "Analysis: Ppk", "oq_repeatable": "Analysis is repeatable", "oq_report": "Report creation", "oq_archive_check": "Archive check of the report",
        "oq_archive_tamper": "A changed archive is found", "oq_builtin_verification": "Built-in verification against published values", "oq_audit_chain": "Audit chain after the actions",
        "oq_backup_restore": "Backup and restore", "oq_persistence": "Data persist across a restart of the database", "oq_error": "The operational checks run",
    },
    "zh-TW": {
        "python_version": "Python 版本", "python_bits": "Python 位元數", "manifest": "發行清單", "manifest_digest": "發行清單完整", "program_version": "程式版本等於發行版",
        "files_intact": "已安裝的檔案就是建置時的檔案", "packages_locked": "套件版本與發行時測試的相同", "web_files": "網頁介面檔案",
        "database": "資料庫檔案", "database_integrity": "資料庫完整性檢查", "database_schema": "資料庫結構版本", "database_audit_chain": "資料庫的稽核鏈",
        "database_permissions": "資料庫檔案權限", "database_create": "可以建立新資料庫", "data_folder_writable": "資料資料夾可寫入", "disk_free": "磁碟可用空間",
        "clock": "時鐘與時區", "oq_page": "網頁與安全標頭", "oq_requires_login": "需要登入", "oq_wrong_password": "錯誤密碼被拒絕", "oq_login": "使用者登入",
        "oq_role_viewer": "角色：檢視者不能匯入", "oq_role_audit": "角色：工程師不能讀稽核紀錄", "oq_import": "資料匯入", "oq_index_mean": "分析：平均值", "oq_index_sd": "分析：標準差",
        "oq_index_p": "分析：Pp", "oq_index_pk": "分析：Ppk", "oq_repeatable": "分析可重現", "oq_report": "報告產生", "oq_archive_check": "報告封存檢查",
        "oq_archive_tamper": "被改過的封存會被發現", "oq_builtin_verification": "內建驗證（對照已發表的數值）", "oq_audit_chain": "操作後的稽核鏈",
        "oq_backup_restore": "備份與還原", "oq_persistence": "資料庫重新開啟後資料仍在", "oq_error": "運作檢查可以執行",
    },
}


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_of(record: dict) -> str:
    return hashlib.sha256(_dump({k: v for k, v in record.items() if k != "digest"}).encode("utf-8")).hexdigest()


def make_record(kind: str, checks: list[dict], host: dict, manifest: dict | None = None, attachments: dict[str, str] | None = None) -> dict:
    count = {s: sum(c["status"] == s for c in checks) for s in STATUS_ORDER}
    verdict = "fail" if count["fail"] else "warn" if count["warn"] else "pass"
    record = {"format": 1, "kind": kind, "tool_version": spc.__version__, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "host": host,
              "release": {"version": spc.__version__, "manifest_digest": (manifest or {}).get("digest"), "built_at": (manifest or {}).get("built_at")},
              "checks": checks, "counts": count, "verdict": verdict, "attachments": attachments or {}}
    record["digest"] = digest_of(record)
    return record


def verify_record(record: dict) -> bool:
    return isinstance(record.get("digest"), str) and digest_of(record) == record["digest"]


def _title(cid: str, lang: str) -> str:
    return CHECK_TITLES[lang].get(cid) or CHECK_TITLES["en"].get(cid) or cid.replace("_", " ")


def render(record: dict, lang: str = "en") -> str:
    T = TEXT[lang]
    esc = html.escape
    c = record["counts"]
    verdict = T["verdict_" + record["verdict"]]
    host = record["host"]
    rows = []
    for ch in sorted(record["checks"], key=lambda x: STATUS_ORDER.index(x["status"]) if x["status"] in STATUS_ORDER else 9):
        rows.append(f"<tr class='{esc(ch['status'])}'><td>{esc(_title(ch['id'], lang))}</td><td>{esc(str(ch['expected']))}</td><td>{esc(str(ch['actual']))}</td>"
                    f"<td class='st'>{esc(T['st_' + ch['status']])}</td><td>{esc(ch.get('note', ''))}</td></tr>")
    sig = "".join(f"<tr><td>{esc(T[k])}</td><td></td><td></td><td></td></tr>" for k in ("executed", "reviewed", "approved"))
    attached = "".join(f"<li>{esc(k)} <code>{esc(v)}</code></li>" for k, v in record["attachments"].items()) or f"<li>{esc(T['none'])}</li>"
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(T['title'][record['kind']])}</title>
<style>
body{{font-family:system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:2rem auto;max-width:62rem;padding:0 1rem;color:#1b1f24;background:#fff}}
h1{{font-size:1.5rem}} table{{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9rem}} th,td{{border:1px solid #c5ccd3;padding:.35rem .5rem;text-align:left;vertical-align:top}}
th{{background:#eef1f4}} tr.fail td{{background:#fde8e8}} tr.warn td{{background:#fff4d6}} td.st{{font-weight:600}}
.verdict{{font-size:1.2rem;font-weight:700;padding:.5rem .8rem;border-radius:.3rem;display:inline-block}} .verdict.pass{{background:#dff3e3}} .verdict.warn{{background:#fff4d6}} .verdict.fail{{background:#fde8e8}}
code{{font-family:ui-monospace,Consolas,monospace;word-break:break-all}} .sig td{{height:2.6rem}} @media print{{body{{margin:0}}}}
</style></head><body>
<h1>{esc(T['title'][record['kind']])}</h1>
<p>{esc(T['intro'])}</p>
<table>
<tr><th>{esc(T['version'])}</th><td>{esc(record['release']['version'])}</td><th>{esc(T['manifest'])}</th><td><code>{esc(str(record['release']['manifest_digest'] or '–'))}</code></td></tr>
<tr><th>{esc(T['host'])}</th><td>{esc(host.get('host', ''))}</td><th>{esc(T['user'])}</th><td>{esc(host.get('user', ''))}</td></tr>
<tr><th>{esc(T['platform'])}</th><td>{esc(host.get('platform', ''))}</td><th>{esc(T['python'])}</th><td>{esc(host.get('python', ''))}</td></tr>
<tr><th>{esc(T['started'])}</th><td>{esc(host.get('started_at', record['created_at']))}</td><th>{esc(T['verdict'])}</th><td><span class="verdict {esc(record['verdict'])}">{esc(verdict)}</span></td></tr>
</table>
<p>{esc(T['summary'].format(p=c['pass'], f=c['fail'], w=c['warn'], i=c['info']))}</p>
<table><tr><th>{esc(T['check'])}</th><th>{esc(T['expected'])}</th><th>{esc(T['actual'])}</th><th>{esc(T['status'])}</th><th>{esc(T['note'])}</th></tr>{''.join(rows)}</table>
<h2>{esc(T['attached'])}</h2><ul>{attached}</ul>
<h2>{esc(T['sign'])}</h2>
<table class="sig"><tr><th></th><th>{esc(T['name'])}</th><th>{esc(T['date'])}</th><th>{esc(T['signature'])}</th></tr>{sig}</table>
<p>{esc(T['digest'])}:<br><code>{esc(record['digest'])}</code></p>
<p>{esc(T['verify'])}</p>
</body></html>
"""
