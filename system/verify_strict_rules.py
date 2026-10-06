#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
TAIWAN_TZ = ZoneInfo("Asia/Taipei")


def fmt(numbers) -> str:
    return " ".join(f"{int(number):02d}" for number in numbers)


def expected_target_date(latest_draw_date: str) -> str:
    latest = datetime.strptime(latest_draw_date, "%Y-%m-%d").date()
    today = datetime.now(TAIWAN_TZ).date()
    if latest >= today:
        return (latest + timedelta(days=1)).isoformat()
    return today.isoformat()


def main() -> int:
    analysis = json.loads((ROOT / "reports" / "latest_analysis.json").read_text(encoding="utf-8"))
    html = (ROOT / "reports" / "latest_battle_report.html").read_text(encoding="utf-8")
    summary = json.loads((ROOT / "data" / "ghana_daywa39_fetch_summary.json").read_text(encoding="utf-8"))
    single = int(analysis["strong_packs"]["strong_single"]["numbers"][0])
    latest_numbers = [int(number) for number in analysis["latest_draw"]["numbers"]]
    latest_date = analysis["latest_draw"]["draw_date"]
    expected_target = expected_target_date(latest_date)
    top9 = [int(item["number"]) for item in analysis["candidates"][:9]]
    low_hit = analysis.get("low_hit_regime_shift") or {}
    failure_memory = low_hit.get("failure_memory") or {}
    front9 = analysis.get("front9_escape_correction") or {}
    optimizer = analysis.get("hit_rate_optimizer") or {}
    high_gate = analysis.get("high_confidence_gate") or {}
    external = analysis.get("external_method_weight_shift") or {}
    ultra = analysis.get("ultra_confidence_pick") or {}
    repair = analysis.get("self_repair_status") or {}
    audit = analysis.get("ironlaw_full_audit") or {}
    mobile_html_path = ROOT / "site" / "full-report.html"
    mobile_html = mobile_html_path.read_text(encoding="utf-8") if mobile_html_path.exists() else ""
    repair_html_path = ROOT / "site" / "repair.html"
    repair_html = repair_html_path.read_text(encoding="utf-8") if repair_html_path.exists() else ""
    package_html_path = ROOT / "cloud_mobile_site" / "package.json"
    package_text = package_html_path.read_text(encoding="utf-8") if package_html_path.exists() else ""
    audit_path = ROOT / "reports" / "ghana39_ironlaw_full_audit.json"
    audit_report = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    rows = list(csv.DictReader((ROOT / "data" / "ghana_daywa39_history.csv").open(encoding="utf-8-sig")))

    exact_539_sections = (
        "本期最強1顆",
        "最強號碼多邏輯總結",
        "本期資料",
        "失準事件監測",
        "本期分級主選",
        "本期前15名單一明細",
        "本期推薦牌組",
        "本期投注排除",
        "上一期號碼連莊資格",
        "使用說明",
    )
    checks = {
        "LatestDate": latest_date,
        "LatestNumbers": fmt(latest_numbers),
        "TargetDate": analysis["target_draw_date"],
        "ExpectedTargetDate": expected_target,
        "StrongSingle": f"{single:02d}",
        "SingleInLatest": single in latest_numbers,
        "Top9": fmt(top9),
        "RollingStatus": analysis["rolling_error_adjustment"]["status"],
        "LowHitStatus": low_hit.get("status"),
        "LowHitMode": low_hit.get("mode"),
        "LowHitSeverity": low_hit.get("severity"),
        "FailureMemory": failure_memory.get("status"),
        "Front9EscapeStatus": front9.get("status"),
        "Front9Promoted": fmt(front9.get("promoted_numbers", [])),
        "Front9Demoted": fmt(front9.get("demoted_numbers", [])),
        "HitRateOptimizer": optimizer.get("status"),
        "HitRatePromoted": fmt(optimizer.get("promoted_numbers", [])),
        "HitRateDemoted": fmt(optimizer.get("demoted_numbers", [])),
        "HighConfidenceGate": high_gate.get("status"),
        "UltraConfidenceStatus": ultra.get("status"),
        "UltraConfidenceSingle": f"{int(ultra.get('number')):02d}" if ultra.get("number") else "-",
        "UltraLogicChecks": len(ultra.get("logic_checks") or []),
        "ExternalMethodShift": external.get("status"),
        "SelfRepairDeadline": repair.get("self_repair_deadline_taiwan"),
        "SelfRepairMobileRefresh": repair.get("mobile_refresh_seconds"),
        "IronlawAuditStatus": audit.get("status"),
        "IronlawAuditFailed": audit.get("failed_count"),
        "IronlawAuditReportStatus": audit_report.get("status"),
        "DataGate": analysis["data_integrity_gate"]["status"],
        "Engine": analysis["engine_version"],
        "HasLatestDate": latest_date in html,
        "HasTop9": fmt(top9) in html,
        "HasSpecLayout": all(text in html for text in exact_539_sections) and ('data-report-mode="539-exact-battle-report"' in html),
        "HasRolling": ("錯誤模組" in html) or ("滾動修正" in html) or ("回灌" in html),
        "HasLowHit": ("低命中" in html) and (("漏抓回補" in html) or ("權重轉換" in html)),
        "HasFront9Escape": (("9名後" in html) or ("第10到15" in html) or ("第10至15" in html)) and (("外溢" in html) or ("拉回" in html)),
        "HasHitRateOptimizer": (("命中率強化" in html) or ("整組命中率" in html)) and ("高機率校準" in html),
        "HasUltraConfidence": ("超高信心高機率推薦" in html) or ("本期綜合最強" in html),
        "HasExternalMethodShift": ("外部模式" in html) and (("配對" in html) or ("companion" in html)),
        "HasSelfRepair": ("自主修復" in html) and ("19:30" in html),
        "HasDailyIronlawSchedule": ("17:30" in html) and ("19:30" in html),
        "HasDecisiveAnswers": ("本期分級主選" in html) and ("1中1" in html) and ("2中1" in html) and ("3中1" in html) and ("5中2" in html) and ("9中3" in html) and ("本期投注排除" in html),
        "HasFullAudit": ("系統健康與公開狀態" in html) and ("稽核狀態" in html),
        "HasTargetDateCorrection": ("預測目標日" in html) and ("歷史資料截止日" in html) and ("官方資料缺口" in mobile_html),
        "HasMobilePageShowRefresh": "pageshow" in mobile_html and "autoRefreshIfStale" in mobile_html,
        "HasManualUpdateButton": ("手動更新最新" in mobile_html) and ("manualUpdateLatest" in mobile_html),
        "HasCloudRepairButton": ("當機立即修復" in mobile_html) and ("repair.html" in mobile_html) and ("ghana39-cloud-self-repair.yml" in repair_html),
        "Has539InterfaceMode": (("539介面模式" in mobile_html) and ('data-report-mode="539-interface"' in mobile_html)) or ('data-report-mode="539-exact-battle-report"' in mobile_html),
        "Has539StandardReport": ('data-report-mode="539-exact-battle-report"' in mobile_html) and all(text in mobile_html for text in exact_539_sections),
        "HasManualUpdateCompletedTime": ("最後手動更新完成" in mobile_html) and ("finalizeManualUpdateIfNeeded" in mobile_html) and ("ghana39_last_manual_update" in mobile_html),
        "BuildScriptCrossPlatform": "WRANGLER_LOG_PATH=" not in package_text,
        "HasDataGate": "稽核狀態" in html,
        "HasSingleGuard": "強烈推薦守門" in html,
        "H2Count": len(re.findall("<h2", html)),
        "HasOldText": "天天樂" in html,
        "TailRows": [
            [row["draw_date"], row["n1"], row["n2"], row["n3"], row["n4"], row["n5"], row["source"]]
            for row in rows[-5:]
        ],
        "FailedModels": analysis["rolling_error_adjustment"]["failed_models_reweighted"],
        "BoostedModels": analysis["rolling_error_adjustment"]["boosted_models_reweighted"],
        "SingleAudit": analysis["strong_packs"]["strong_single"].get("selection_audit"),
    }
    for key, value in checks.items():
        print(f"{key}: {value}")
    assert latest_date == summary.get("latest_draw_date")
    assert analysis["target_draw_date"] == expected_target
    assert single not in latest_numbers
    assert analysis["rolling_error_adjustment"]["status"] == "applied"
    assert low_hit.get("status") in {"critical_shift", "watch_shift", "normal", "no_settled_history"}
    assert failure_memory.get("status") in {"active", "inactive"}
    assert front9.get("status") in {"applied", "reviewed_no_swap", "inactive"}
    assert optimizer.get("status") in {"applied", "reviewed_no_change", "inactive"}
    assert high_gate.get("status") in {"passed", "blocked"}
    assert ultra.get("number") == single
    assert ultra.get("status") in {"ultra_high_confidence_recommendation", "strongest_research_signal"}
    assert len(ultra.get("logic_checks") or []) >= 6
    assert external.get("status") in {"applied", "not_applied"}
    assert repair.get("self_repair_deadline_taiwan") == "19:30"
    assert int(repair.get("mobile_refresh_seconds") or 0) <= 30
    assert audit.get("status") in {"passed", "pending"}
    if audit_report:
        assert audit_report.get("status") == "passed"
        assert int(audit_report.get("failed_count") or 0) == 0
    assert analysis["data_integrity_gate"]["status"] == "passed"
    assert checks["HasRolling"]
    assert checks["HasSpecLayout"]
    assert checks["HasLowHit"]
    assert checks["HasFront9Escape"]
    assert checks["HasHitRateOptimizer"]
    assert checks["HasUltraConfidence"]
    assert checks["HasExternalMethodShift"]
    assert checks["HasSelfRepair"]
    assert checks["HasDailyIronlawSchedule"]
    assert checks["HasDecisiveAnswers"]
    assert checks["HasFullAudit"]
    assert checks["HasTargetDateCorrection"]
    assert checks["HasMobilePageShowRefresh"]
    assert checks["HasManualUpdateButton"]
    assert checks["HasCloudRepairButton"]
    assert checks["Has539InterfaceMode"]
    assert checks["Has539StandardReport"]
    assert checks["HasManualUpdateCompletedTime"]
    assert checks["BuildScriptCrossPlatform"]
    assert checks["HasDataGate"]
    assert checks["HasSingleGuard"]
    assert not checks["HasOldText"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
