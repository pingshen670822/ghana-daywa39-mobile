#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def fmt(numbers) -> str:
    return " ".join(f"{int(number):02d}" for number in numbers)


def main() -> int:
    analysis = json.loads((ROOT / "reports" / "latest_analysis.json").read_text(encoding="utf-8"))
    html = (ROOT / "reports" / "latest_battle_report.html").read_text(encoding="utf-8")
    summary = json.loads((ROOT / "data" / "ghana_daywa39_fetch_summary.json").read_text(encoding="utf-8"))
    single = int(analysis["strong_packs"]["strong_single"]["numbers"][0])
    latest_numbers = [int(number) for number in analysis["latest_draw"]["numbers"]]
    latest_date = analysis["latest_draw"]["draw_date"]
    top9 = [int(item["number"]) for item in analysis["candidates"][:9]]
    low_hit = analysis.get("low_hit_regime_shift") or {}
    failure_memory = low_hit.get("failure_memory") or {}
    front9 = analysis.get("front9_escape_correction") or {}
    rows = list(csv.DictReader((ROOT / "data" / "ghana_daywa39_history.csv").open(encoding="utf-8-sig")))

    checks = {
        "LatestDate": latest_date,
        "LatestNumbers": fmt(latest_numbers),
        "TargetDate": analysis["target_draw_date"],
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
        "DataGate": analysis["data_integrity_gate"]["status"],
        "Engine": analysis["engine_version"],
        "HasLatestDate": latest_date in html,
        "HasTop9": fmt(top9) in html,
        "HasRolling": ("錯誤模組" in html) or ("滾動修正" in html),
        "HasLowHit": ("低命中" in html) and (("漏抓回補" in html) or ("權重轉換" in html)),
        "HasFront9Escape": (("9名後" in html) or ("第10到15" in html)) and (("外溢" in html) or ("拉回前九" in html)),
        "HasDataGate": "資料真實性" in html,
        "HasSingleGuard": "獨隻守門" in html,
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
    assert single not in latest_numbers
    assert analysis["rolling_error_adjustment"]["status"] == "applied"
    assert low_hit.get("status") in {"critical_shift", "watch_shift", "normal", "no_settled_history"}
    assert failure_memory.get("status") in {"active", "inactive"}
    assert front9.get("status") in {"applied", "reviewed_no_swap", "inactive"}
    assert analysis["data_integrity_gate"]["status"] == "passed"
    assert checks["HasRolling"]
    assert checks["HasLowHit"]
    assert checks["HasFront9Escape"]
    assert checks["HasDataGate"]
    assert checks["HasSingleGuard"]
    assert not checks["HasOldText"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
