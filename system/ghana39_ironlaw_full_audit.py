#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
SITE_DIR = ROOT / "site"
CLOUD_PUBLIC_DIR = ROOT / "cloud_mobile_site" / "public"
DB_PATH = DATA_DIR / "ghana_daywa39.sqlite"
CSV_PATH = DATA_DIR / "ghana_daywa39_history.csv"
FETCH_SUMMARY_PATH = DATA_DIR / "ghana_daywa39_fetch_summary.json"
ANALYSIS_PATH = REPORT_DIR / "latest_analysis.json"
SITE_ANALYSIS_PATH = SITE_DIR / "latest_analysis.json"
REPORT_HTML_PATH = REPORT_DIR / "latest_battle_report.html"
SITE_FULL_REPORT_PATH = SITE_DIR / "full-report.html"
SITE_VERSION_PATH = SITE_DIR / "version.json"
SYNC_STATUS_PATH = DATA_DIR / "sync_status.json"
SELF_REPAIR_STATUS_PATH = DATA_DIR / "self_repair_status.json"
AUDIT_JSON_PATH = REPORT_DIR / "ghana39_ironlaw_full_audit.json"
AUDIT_MD_PATH = REPORT_DIR / "ghana39_ironlaw_full_audit.md"
TAIWAN_TZ = ZoneInfo("Asia/Taipei")
FORBIDDEN_OLD_TEXT = "天天樂"
PUBLIC_CLOUD_URL = "https://ghana-daywa39-ironlaw-mobile.ping-shen670888.chatgpt.site/full-report.html"
PUBLIC_GITHUB_URL = "https://pingshen670822.github.io/ghana-daywa39-mobile/full-report.html"


def now_taiwan() -> str:
    return datetime.now(TAIWAN_TZ).isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def fmt(numbers) -> str:
    return " ".join(f"{int(number):02d}" for number in numbers)


def valid_numbers(numbers) -> bool:
    clean = [int(number) for number in numbers]
    return len(clean) == 5 and len(set(clean)) == 5 and all(1 <= number <= 39 for number in clean)


def expected_target_date(latest_draw_date: str) -> str:
    latest = datetime.strptime(latest_draw_date, "%Y-%m-%d").date()
    today = datetime.now(TAIWAN_TZ).date()
    if latest >= today:
        return (latest + timedelta(days=1)).isoformat()
    return today.isoformat()


def add(checks: list[dict], item: str, status: str, message: str, detail=None) -> None:
    checks.append(
        {
            "item": item,
            "status": status,
            "passed": status in {"passed", "warning"},
            "message": message,
            "detail": detail,
        }
    )


def csv_rows() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def latest_csv_draw(rows: list[dict]) -> dict:
    if not rows:
        return {}
    row = rows[-1]
    return {
        "draw_date": row.get("draw_date"),
        "numbers": [int(row[f"n{i}"]) for i in range(1, 6)],
        "source": row.get("source", ""),
    }


def db_latest_draw() -> dict:
    if not DB_PATH.exists():
        return {}
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT draw_date,n1,n2,n3,n4,n5,source FROM draws ORDER BY draw_date DESC LIMIT 1").fetchone()
        if not row:
            return {}
        return {"draw_date": row[0], "numbers": [int(number) for number in row[1:6]], "source": row[6]}


def db_duplicate_dates() -> list[str]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT draw_date,COUNT(*) FROM draws GROUP BY draw_date HAVING COUNT(*) > 1 ORDER BY draw_date"
        ).fetchall()
    return [row[0] for row in rows]


def db_stale_pending(latest_date: str) -> list[str]:
    if not DB_PATH.exists() or not latest_date:
        return []
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT based_on_date,target_date FROM predictions WHERE status='pending' AND target_date <= ? ORDER BY target_date",
            (latest_date,),
        ).fetchall()
    return [f"{row[0]}->{row[1]}" for row in rows]


def scheduled_task_exists(name: str) -> bool:
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Get-ScheduledTask -TaskName {json.dumps(name)} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return name in (result.stdout or "")


def compact_audit_payload(payload: dict) -> dict:
    return {
        "status": payload.get("status"),
        "generated_at_taiwan": payload.get("generated_at_taiwan"),
        "passed_count": payload.get("passed_count"),
        "failed_count": payload.get("failed_count"),
        "warning_count": payload.get("warning_count"),
        "latest_draw_date": payload.get("latest_draw_date"),
        "target_draw_date": payload.get("target_draw_date"),
        "public_cloud_url": payload.get("public_cloud_url"),
        "public_github_url": payload.get("public_github_url"),
        "rule": "539鐵律同級全系統稽核：資料、戰報、手機雲端、排程、自主修復、禁用舊內容全部過關才允許發布。",
    }


def update_analysis_with_audit(payload: dict) -> None:
    compact = compact_audit_payload(payload)
    for path in (ANALYSIS_PATH, SITE_ANALYSIS_PATH):
        data = load_json(path)
        if not data:
            continue
        data["ironlaw_full_audit"] = compact
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_pointer_files(payload: dict) -> None:
    version = payload.get("version") or ""
    report_url = f"{PUBLIC_CLOUD_URL}?v={version}" if version else PUBLIC_CLOUD_URL
    status = {
        "status": payload.get("status"),
        "generated_at_taiwan": payload.get("generated_at_taiwan"),
        "latest_draw_date": payload.get("latest_draw_date"),
        "target_draw_date": payload.get("target_draw_date"),
        "report_url": report_url,
        "public_cloud_url": PUBLIC_CLOUD_URL,
        "public_github_url": PUBLIC_GITHUB_URL,
        "mobile_independent": True,
        "audit_json": str(AUDIT_JSON_PATH),
    }
    files = {
        DATA_DIR / "手機戰報更新狀態.json": status,
        DATA_DIR / "手機雲端發布狀態.json": status,
    }
    for path, body in files.items():
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    for path, value in {
        DATA_DIR / "手機獨立版網址.txt": report_url,
        DATA_DIR / "手機雲端版網址.txt": report_url,
        DATA_DIR / "手機戰報即時網址.txt": report_url,
        DATA_DIR / "手機控制台網址.txt": f"{PUBLIC_CLOUD_URL.replace('/full-report.html', '')}/system.html?v={version}" if version else PUBLIC_CLOUD_URL,
    }.items():
        path.write_text(value + "\n", encoding="utf-8")


def build_markdown(payload: dict) -> str:
    lines = [
        "# 迦納彩39 全系統鐵律稽核",
        "",
        f"- 產生時間：{payload.get('generated_at_taiwan')}",
        f"- 狀態：{payload.get('status')}",
        f"- 最新開獎日：{payload.get('latest_draw_date')}",
        f"- 下期目標日：{payload.get('target_draw_date')}",
        f"- 手機雲端戰報：{payload.get('public_cloud_url')}",
        "",
        "| 項目 | 狀態 | 說明 |",
        "| --- | --- | --- |",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| {check.get('item')} | {check.get('status')} | {check.get('message')} |")
    return "\n".join(lines) + "\n"


def mirror_to_site() -> None:
    if SITE_DIR.exists():
        (SITE_DIR / AUDIT_JSON_PATH.name).write_text(AUDIT_JSON_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        (SITE_DIR / AUDIT_MD_PATH.name).write_text(AUDIT_MD_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    if CLOUD_PUBLIC_DIR.exists():
        (CLOUD_PUBLIC_DIR / AUDIT_JSON_PATH.name).write_text(AUDIT_JSON_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        (CLOUD_PUBLIC_DIR / AUDIT_MD_PATH.name).write_text(AUDIT_MD_PATH.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []
    analysis = load_json(ANALYSIS_PATH)
    site_analysis = load_json(SITE_ANALYSIS_PATH)
    version = load_json(SITE_VERSION_PATH)
    fetch_summary = load_json(FETCH_SUMMARY_PATH)
    sync_status = load_json(SYNC_STATUS_PATH)
    repair_status = load_json(SELF_REPAIR_STATUS_PATH)
    report_html = read_text(REPORT_HTML_PATH)
    site_html = read_text(SITE_FULL_REPORT_PATH)
    mobile_html = site_html + read_text(SITE_DIR / "prediction.html") + read_text(SITE_DIR / "clear-cache.html")
    rows = csv_rows()
    csv_latest = latest_csv_draw(rows)
    db_latest = db_latest_draw()
    latest = analysis.get("latest_draw") or {}
    latest_date = latest.get("draw_date")
    latest_numbers = [int(number) for number in latest.get("numbers", [])] if latest.get("numbers") else []
    top9 = [int(item["number"]) for item in (analysis.get("candidates") or [])[:9]]
    top15 = [int(item["number"]) for item in (analysis.get("candidates") or [])[:15]]
    single = (((analysis.get("strong_packs") or {}).get("strong_single") or {}).get("numbers") or [None])[0]
    ultra = analysis.get("ultra_confidence_pick") or {}
    ironlaw = analysis.get("ironlaw_spec") or {}
    self_repair = analysis.get("self_repair_status") or {}

    add(checks, "CSV歷史資料存在", "passed" if rows else "failed", f"CSV rows={len(rows)}")
    invalid_rows = []
    duplicate_dates = []
    seen_dates = set()
    previous_date = ""
    for row in rows:
        draw_date = row.get("draw_date", "")
        try:
            numbers = [int(row[f"n{i}"]) for i in range(1, 6)]
        except (TypeError, ValueError, KeyError):
            numbers = []
        if draw_date in seen_dates:
            duplicate_dates.append(draw_date)
        seen_dates.add(draw_date)
        if previous_date and draw_date <= previous_date:
            invalid_rows.append(f"date_order:{draw_date}")
        previous_date = draw_date
        if not valid_numbers(numbers):
            invalid_rows.append(f"invalid_numbers:{draw_date}")
        if not row.get("source"):
            invalid_rows.append(f"missing_source:{draw_date}")
    add(checks, "CSV開獎格式", "passed" if not invalid_rows and not duplicate_dates else "failed", "5顆號碼、1-39、日期排序、來源欄位", {"invalid": invalid_rows[:20], "duplicate": duplicate_dates[:20]})
    add(checks, "SQLite資料庫存在", "passed" if DB_PATH.exists() and db_latest else "failed", str(DB_PATH))
    add(checks, "CSV與SQLite最新一致", "passed" if csv_latest and db_latest and csv_latest == db_latest else "failed", f"CSV {csv_latest.get('draw_date')} / DB {db_latest.get('draw_date')}")
    add(checks, "SQLite重複日期", "passed" if not db_duplicate_dates() else "failed", "不得有重複開獎日期", db_duplicate_dates()[:20])
    add(checks, "最新分析存在", "passed" if analysis else "failed", str(ANALYSIS_PATH))
    add(checks, "最新開獎號碼合法", "passed" if valid_numbers(latest_numbers) else "failed", fmt(latest_numbers) if latest_numbers else "-")
    add(checks, "分析與CSV最新一致", "passed" if latest_date and csv_latest and latest_date == csv_latest.get("draw_date") and latest_numbers == csv_latest.get("numbers") else "failed", f"analysis {latest_date} {fmt(latest_numbers) if latest_numbers else '-'}")
    expected_target = expected_target_date(latest_date) if latest_date else None
    add(checks, "預測目標開獎日校正", "passed" if expected_target and analysis.get("target_draw_date") == expected_target else "failed", f"target={analysis.get('target_draw_date')} expected={expected_target}; 官方最新日只作資料依據，預測目標依台灣當前17:30開獎日校正")
    summary_latest = fetch_summary.get("latest_draw_date")
    add(checks, "官方抓取摘要一致", "passed" if not summary_latest or summary_latest == latest_date else "failed", f"fetch_summary={summary_latest} analysis={latest_date}")
    if fetch_summary.get("latest_draw_date"):
        add(checks, "官方公開資料更新時間", "passed", f"latest={fetch_summary.get('latest_draw_date')} updated={fetch_summary.get('updated_at_taiwan')}")
    else:
        add(checks, "官方公開資料更新時間", "warning", "官方抓取摘要尚未取得最新日期")

    add(checks, "候選前九完整", "passed" if len(top9) == 9 and valid_numbers(top9[:5]) and len(set(top9)) == 9 and all(1 <= n <= 39 for n in top9) else "failed", fmt(top9) if top9 else "-")
    add(checks, "候選前十五完整", "passed" if len(top15) == 15 and len(set(top15)) == 15 and all(1 <= n <= 39 for n in top15) else "failed", fmt(top15) if top15 else "-")
    add(checks, "最強獨隻守門", "passed" if single and int(single) not in latest_numbers and ultra.get("number") == single and len(ultra.get("logic_checks") or []) >= 6 else "failed", f"single={int(single):02d}" if single else "-")
    add(checks, "強牌組分層", "passed" if all(key in (analysis.get("strong_packs") or {}) for key in ("strong_single", "two_hit_one", "three_hit_one", "five_hit_two", "nine_hit_three")) else "failed", "獨隻、2中1、3中1、5中2、9中3")
    add(checks, "低機率三層", "passed" if all(key in (analysis.get("low_probability") or {}) for key in ("avoid_5", "avoid_10", "avoid_15")) else "failed", "5不中、10不中、15不中")
    add(checks, "資料真實性守門", "passed" if (analysis.get("data_integrity_gate") or {}).get("status") == "passed" else "failed", (analysis.get("data_integrity_gate") or {}).get("rule", "-"))
    add(checks, "滾動重算啟用", "passed" if (analysis.get("rolling_error_adjustment") or {}).get("status") == "applied" else "failed", "開獎後錯誤模組重新加權")
    add(checks, "命中率強化啟用", "passed" if (analysis.get("hit_rate_optimizer") or {}).get("status") in {"applied", "reviewed_no_change", "inactive"} else "failed", (analysis.get("hit_rate_optimizer") or {}).get("rule", "-"))
    add(checks, "9名後外溢檢查", "passed" if (analysis.get("front9_escape_correction") or {}).get("status") in {"applied", "reviewed_no_swap", "inactive"} else "failed", (analysis.get("front9_escape_correction") or {}).get("rule", "-"))
    add(checks, "上期結算沒有過期待處理", "passed" if not db_stale_pending(latest_date) else "failed", "pending target_date不得小於等於最新開獎日", db_stale_pending(latest_date)[:20])
    add(checks, "鐵律規格欄位", "passed" if all(key in ironlaw for key in ("data_first", "settlement", "front9_escape_correction", "self_repair_after_draw", "publish_block_gate")) else "failed", "539鐵律同級規格鍵值")

    required_html = [
        "標準戰報規格導覽",
        "539鐵律同級",
        "每日更新鐵律時間表",
        "本期明確作戰答案",
        "明確獨支",
        "明確2中1",
        "明確3中1",
        "明確5中2",
        "明確9中3",
        "防守避開",
        "超高信心高機率推薦",
        "強烈推薦單號",
        "強牌組",
        "逐號解析",
        "命中率強化",
        "自主修復",
        "19:30",
        "資料真實性",
        "獨隻守門",
    ]
    add(checks, "桌面戰報規格", "passed" if all(text in report_html for text in required_html) else "failed", "必含539鐵律同級戰報區塊")
    add(checks, "手機完整戰報規格", "passed" if all(text in site_html for text in required_html) else "failed", "手機獨立頁必含完整戰報")
    add(checks, "手機即時刷新", "passed" if all(text in mobile_html for text in ("version.json", "pageshow", "autoRefreshIfStale", "clearMobileCaches")) else "failed", "手機開啟、回前景、恢復連線立即檢查版本")
    add(checks, "禁用舊品牌字樣", "passed" if FORBIDDEN_OLD_TEXT not in report_html + site_html else "failed", "戰報與手機頁不得出現舊字樣")
    add(checks, "站台JSON同步", "passed" if site_analysis and site_analysis.get("generated_at_taiwan") == analysis.get("generated_at_taiwan") else "failed", "site/latest_analysis.json 必須與 reports/latest_analysis.json 同版")
    add(checks, "版本JSON同步", "passed" if version.get("latest_draw_date") == latest_date and version.get("independent_mobile") is True else "failed", "version.json 必須指向最新獨立手機版")
    repo_public_ready = (ROOT.parent / "full-report.html").exists()
    local_cloud_ready = CLOUD_PUBLIC_DIR.exists() and (CLOUD_PUBLIC_DIR / "full-report.html").exists()
    add(checks, "雲端來源同步", "passed" if local_cloud_ready or repo_public_ready else "failed", "cloud_mobile_site/public 或 GitHub Pages 根目錄必須可獨立部署")
    add(checks, "自動更新排程", "passed" if scheduled_task_exists("Ghana39 Daywa Auto Update Publish") else "failed", "每日17:31更新發布排程")
    add(checks, "自主修復排程", "passed" if scheduled_task_exists("Ghana39 Daywa Self Repair Check") else "failed", "每日19:31自主修復排程")
    add(checks, "自動更新鐵律時間", "passed" if self_repair.get("daily_draw_time_taiwan") == "17:30" and self_repair.get("auto_update_task_time_taiwan") == "17:31" and self_repair.get("self_repair_deadline_taiwan") == "19:30" else "failed", "17:30開獎、17:31更新、19:30故障門檻")
    add(checks, "同步狀態紀錄", "passed" if sync_status.get("status") in {"synced", "accepted", "waiting", "timeout", "not_ready"} or not sync_status else "warning", sync_status.get("status", "尚未有同步紀錄"))
    add(checks, "自主修復狀態紀錄", "passed" if repair_status.get("status") in {"healthy", "repairing", "repaired", "failed"} or not repair_status else "warning", repair_status.get("status", "尚未有修復紀錄"))

    failed = [check for check in checks if check["status"] == "failed"]
    warnings = [check for check in checks if check["status"] == "warning"]
    payload = {
        "status": "passed" if not failed else "failed",
        "generated_at_taiwan": now_taiwan(),
        "latest_draw_date": latest_date,
        "latest_numbers": latest_numbers,
        "target_draw_date": analysis.get("target_draw_date"),
        "version": version.get("version") or re.sub(r"\D", "", analysis.get("generated_at_taiwan", ""))[:14],
        "public_cloud_url": PUBLIC_CLOUD_URL,
        "public_github_url": PUBLIC_GITHUB_URL,
        "passed_count": sum(1 for check in checks if check["status"] == "passed"),
        "failed_count": len(failed),
        "warning_count": len(warnings),
        "checks": checks,
    }
    AUDIT_JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    AUDIT_MD_PATH.write_text(build_markdown(payload), encoding="utf-8")
    update_analysis_with_audit(payload)
    write_pointer_files(payload)
    mirror_to_site()
    print(json.dumps(compact_audit_payload(payload), ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
