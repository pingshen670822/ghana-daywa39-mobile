#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import california_gana39_system as system
import ghana39_ironlaw_full_audit as audit
import update_ghana_history as history
import verify_strict_rules as verify


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
TAIWAN_TZ = ZoneInfo("Asia/Taipei")
STATUS_PATH = ROOT / "data" / "cloud_self_repair_status.json"


def now_taiwan() -> str:
    return datetime.now(TAIWAN_TZ).isoformat(timespec="seconds")


def write_status(payload: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at_taiwan"] = now_taiwan()
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPO_ROOT / "cloud_self_repair_status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def copy_site_to_repo_root() -> None:
    if not system.SITE_DIR.exists():
        raise RuntimeError(f"Missing site folder: {system.SITE_DIR}")
    for source in system.SITE_DIR.iterdir():
        if not source.is_file():
            continue
        shutil.copy2(source, REPO_ROOT / source.name)
    for report_name in ("ghana39_ironlaw_full_audit.json", "ghana39_ironlaw_full_audit.md"):
        source = system.REPORT_DIR / report_name
        if source.exists():
            shutil.copy2(source, REPO_ROOT / report_name)
    (REPO_ROOT / ".nojekyll").write_text("", encoding="utf-8")


def main() -> int:
    start = now_taiwan()
    end = (datetime.now(TAIWAN_TZ).date() + timedelta(days=1)).isoformat()
    write_status({"status": "running", "started_at_taiwan": start, "message": "雲端自我修復開始"})
    update_code = history.main(["--end", end, "--sleep", "0.05"])
    if update_code != 0:
        write_status({"status": "failed", "stage": "update_history", "exit_code": update_code})
        return update_code
    analysis = system.run(system.DEFAULT_CSV, rounds=120, import_only=False)
    copy_site_to_repo_root()
    audit_code = audit.main()
    copy_site_to_repo_root()
    if audit_code != 0:
        write_status({"status": "failed", "stage": "ironlaw_audit", "exit_code": audit_code})
        return audit_code
    try:
        verify.main()
    except Exception as exc:
        write_status({"status": "failed", "stage": "strict_verify", "message": str(exc)})
        return 1
    write_status(
        {
            "status": "repaired",
            "started_at_taiwan": start,
            "latest_draw_date": analysis["latest_draw"]["draw_date"],
            "target_draw_date": analysis["target_draw_date"],
            "top9": [item["number"] for item in analysis["candidates"][:9]],
            "message": "雲端自我修復完成：官方資料、戰報、手機頁、鐵律稽核已更新。",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
