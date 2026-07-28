#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ghana Daywa 5/39 Direct prediction system.

This is a compact, standalone implementation that follows the Ghana39
iron-law specification: data first, multi-window scoring, model arbitration,
strong-pack governance, previous prediction settlement, transparent reports,
and explicit freshness/risk labeling.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

import ghana39_standard_report as standard_report


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"
SITE_DIR = ROOT / "site"
DB_PATH = DATA_DIR / "ghana_daywa39.sqlite"
DEFAULT_CSV = DATA_DIR / "ghana_daywa39_history.csv"
FETCH_SUMMARY_JSON = DATA_DIR / "ghana_daywa39_fetch_summary.json"
HISTORY_GAP_AUDIT_JSON = DATA_DIR / "ghana_daywa39_history_gap_audit.json"
LATEST_JSON = REPORT_DIR / "latest_analysis.json"
BATTLE_MD = REPORT_DIR / "latest_battle_report.md"
BATTLE_HTML = REPORT_DIR / "latest_battle_report.html"
PRECISION_HTML = REPORT_DIR / "ghana39_precision_battle_report.html"
DESIGN_MD = REPORT_DIR / "system_design.md"
MOBILE_INDEX = SITE_DIR / "index.html"
MOBILE_JSON = SITE_DIR / "latest_analysis.json"


@dataclass(frozen=True)
class GameSpec:
    code: str = "GH_DAYWA39"
    display_name: str = "非洲迦納彩 Daywa 5/39 Direct"
    number_min: int = 1
    number_max: int = 39
    draw_size: int = 5
    draw_timezone: str = "Asia/Taipei"
    report_timezone: str = "Asia/Taipei"
    daily_draw: bool = True
    draw_time_taiwan: str = "17:30"
    safe_taiwan_update_time: str = "17:30"
    official_reference: str = "https://www.nla.com.gh/winning-numbers"


SPEC = GameSpec()
NUMBERS = list(range(SPEC.number_min, SPEC.number_max + 1))
RANDOM_TOP9 = SPEC.draw_size * 9 / SPEC.number_max


MODEL_LABELS = {
    "multi_window_frequency": "多窗口頻率",
    "omission_phase": "遺漏相位",
    "pair_lift": "拖牌關聯",
    "shape_follow": "牌型跟隨",
    "tail_zone_balance": "尾數區間平衡",
    "sum_band_neighbor": "和值鄰近",
    "trend_break": "趨勢轉折",
    "date_cycle": "日期循環",
}

BASE_WEIGHTS = {
    "multi_window_frequency": 0.18,
    "omission_phase": 0.14,
    "pair_lift": 0.16,
    "shape_follow": 0.14,
    "tail_zone_balance": 0.12,
    "sum_band_neighbor": 0.10,
    "trend_break": 0.10,
    "date_cycle": 0.06,
}

ROLLING_REVIEW_WINDOWS = (12, 30, 90)
LOW_HIT_REVIEW_WINDOWS = (5, 10, 20)
FRONT9_ESCAPE_REVIEW_LIMIT = 24


def setup_dirs() -> None:
    for path in (DATA_DIR, REPORT_DIR, LOG_DIR, SITE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now_taiwan() -> datetime:
    return datetime.now(tz(SPEC.report_timezone))


def now_draw_timezone() -> datetime:
    return datetime.now(tz(SPEC.draw_timezone))


def stamp(dt: datetime | None = None) -> str:
    return (dt or now_taiwan()).isoformat(timespec="seconds")


def log(message: str) -> None:
    setup_dirs()
    line = f"{stamp()} {message}"
    print(line)
    with (LOG_DIR / "system.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def fmt_numbers(numbers) -> str:
    return " ".join(f"{int(number):02d}" for number in numbers)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS draws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draw_date TEXT NOT NULL UNIQUE,
            n1 INTEGER NOT NULL,
            n2 INTEGER NOT NULL,
            n3 INTEGER NOT NULL,
            n4 INTEGER NOT NULL,
            n5 INTEGER NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            based_on_date TEXT NOT NULL UNIQUE,
            target_date TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            strong_packs_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            settled_at TEXT,
            actual_date TEXT,
            actual_numbers_json TEXT,
            top5_hits INTEGER,
            top9_hits INTEGER,
            top10_hits INTEGER,
            top15_hits INTEGER,
            strong_pack_hits_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            based_on_date TEXT NOT NULL,
            target_date TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            strong_packs_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            snapshot_reason TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS update_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            message TEXT
        )
        """
    )
    conn.commit()


def valid_numbers(numbers) -> bool:
    clean = [int(number) for number in numbers]
    return (
        len(clean) == SPEC.draw_size
        and len(set(clean)) == SPEC.draw_size
        and all(SPEC.number_min <= number <= SPEC.number_max for number in clean)
    )


def clean_numbers(numbers) -> list[int]:
    return sorted(int(number) for number in numbers)


def upsert_draw(conn: sqlite3.Connection, draw_date: str, numbers, source: str) -> bool:
    numbers = clean_numbers(numbers)
    if not valid_numbers(numbers):
        return False
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO draws(draw_date,n1,n2,n3,n4,n5,source,created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (draw_date, *numbers, source, stamp()),
    )
    return cursor.rowcount > 0


def parse_date_text(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    value = value.replace("/", "-")
    for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%m-%d-%y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


def row_value(row: dict, *names: str) -> str:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def parse_csv_row(row: dict) -> tuple[str, list[int], str] | None:
    draw_date = parse_date_text(row_value(row, "draw_date", "date", "draw date", "DrawDate"))
    if not draw_date:
        return None

    fields = ("n1", "n2", "n3", "n4", "n5")
    if all(row_value(row, field) for field in fields):
        try:
            numbers = [int(row_value(row, field)) for field in fields]
        except ValueError:
            return None
    else:
        blob = " ".join(str(value) for value in row.values())
        found = []
        for token in blob.replace(",", " ").replace("-", " ").split():
            if token.isdigit():
                number = int(token)
                if SPEC.number_min <= number <= SPEC.number_max:
                    found.append(number)
        numbers = found[-SPEC.draw_size :]

    source = row_value(row, "source") or "csv_import"
    if not valid_numbers(numbers):
        return None
    return draw_date, clean_numbers(numbers), source


def import_history_csv(conn: sqlite3.Connection, csv_path: Path) -> dict:
    if not csv_path.exists():
        return {"path": str(csv_path), "status": "missing", "added": 0, "read": 0, "skipped": 0}
    added = 0
    read = 0
    skipped = 0
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            read += 1
            parsed = parse_csv_row(row)
            if not parsed:
                skipped += 1
                continue
            draw_date, numbers, source = parsed
            if upsert_draw(conn, draw_date, numbers, source):
                added += 1
    conn.commit()
    return {"path": str(csv_path), "status": "ok", "added": added, "read": read, "skipped": skipped}


def fetch_draws(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT draw_date,n1,n2,n3,n4,n5,source FROM draws ORDER BY draw_date"
    ).fetchall()
    return [
        {"draw_date": row[0], "numbers": [int(x) for x in row[1:6]], "source": row[6] or ""}
        for row in rows
    ]


def next_draw_date(draw_date: str) -> str:
    return (datetime.strptime(draw_date, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()


def target_taiwan_safe_time(target_date: str) -> str:
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    return f"{target.isoformat()} {SPEC.safe_taiwan_update_time}"


def frequency(draws: list[dict]) -> Counter:
    counter = Counter()
    for draw in draws:
        counter.update(int(number) for number in draw["numbers"])
    return counter


def current_gaps(draws: list[dict]) -> dict[int, int]:
    last_seen = {number: None for number in NUMBERS}
    for index, draw in enumerate(draws):
        for number in draw["numbers"]:
            last_seen[int(number)] = index
    latest_index = len(draws) - 1
    return {
        number: latest_index - last_seen[number] if last_seen[number] is not None else len(draws)
        for number in NUMBERS
    }


def normalize(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {number: 0.0 for number in NUMBERS}
    low = min(values.values())
    high = max(values.values())
    if math.isclose(high, low):
        return {number: 0.0 for number in NUMBERS}
    return {number: (float(values.get(number, 0.0)) - low) / (high - low) for number in NUMBERS}


def normalize_any(values: dict) -> dict:
    if not values:
        return {}
    low = min(values.values())
    high = max(values.values())
    if math.isclose(high, low):
        return {key: 0.0 for key in values}
    return {key: (float(value) - low) / (high - low) for key, value in values.items()}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rank_values(values: dict[int, float]) -> list[int]:
    return sorted(NUMBERS, key=lambda number: (values.get(number, 0.0), -number), reverse=True)


def zone_label(number: int) -> str:
    if number <= 10:
        return "01-10"
    if number <= 20:
        return "11-20"
    if number <= 30:
        return "21-30"
    return "31-39"


def draw_profile(numbers) -> dict:
    ordered = sorted(int(number) for number in numbers)
    zones = Counter(zone_label(number) for number in ordered)
    tails = {number % 10 for number in ordered}
    return {
        "odd": sum(number % 2 for number in ordered),
        "high": sum(1 for number in ordered if number >= 20),
        "sum": sum(ordered),
        "span": ordered[-1] - ordered[0],
        "tail_diversity": len(tails),
        "zones": [zones.get(label, 0) for label in ("01-10", "11-20", "21-30", "31-39")],
    }


def profile_similarity(left: dict, right: dict) -> float:
    zone_gap = sum(abs(a - b) for a, b in zip(left["zones"], right["zones"])) / 10
    score = 1.0
    score -= abs(left["odd"] - right["odd"]) / SPEC.draw_size * 0.16
    score -= abs(left["high"] - right["high"]) / SPEC.draw_size * 0.14
    score -= abs(left["sum"] - right["sum"]) / 120 * 0.22
    score -= abs(left["span"] - right["span"]) / 38 * 0.16
    score -= abs(left["tail_diversity"] - right["tail_diversity"]) / SPEC.draw_size * 0.10
    score -= zone_gap * 0.22
    return max(0.0, score)


def multi_window_frequency_scores(draws: list[dict]) -> dict[int, float]:
    windows = [(5, 0.08), (10, 0.10), (20, 0.15), (50, 0.18), (100, 0.18), (300, 0.17), (720, 0.14)]
    values = {number: 0.0 for number in NUMBERS}
    for window, weight in windows:
        subset = draws[-window:] if len(draws) >= window else draws
        counts = frequency(subset)
        scores = normalize({number: counts.get(number, 0) for number in NUMBERS})
        for number in NUMBERS:
            values[number] += scores[number] * weight
    return normalize(values)


def omission_phase_scores(draws: list[dict]) -> dict[int, float]:
    gaps = current_gaps(draws)
    expected_gap = SPEC.number_max / SPEC.draw_size
    values = {}
    for number, gap in gaps.items():
        phase = gap / expected_gap
        if phase < 0.45:
            value = phase * 0.18
        elif phase <= 2.25:
            value = 1.0 - abs(phase - 1.15) * 0.36
        else:
            value = max(0.35, 1.0 - (phase - 2.25) * 0.12)
        values[number] = max(0.0, value) + math.log1p(gap) * 0.08
    return normalize(values)


def pair_lift_scores(draws: list[dict], window: int = 1800) -> dict[int, float]:
    subset = draws[-window:] if len(draws) > window else draws
    latest = set(draws[-1]["numbers"])
    if len(subset) < 3:
        return {number: 0.0 for number in NUMBERS}
    target_total = Counter()
    anchor_total = Counter()
    transition = defaultdict(Counter)
    for index in range(len(subset) - 1):
        current = set(subset[index]["numbers"])
        following = set(subset[index + 1]["numbers"])
        target_total.update(following)
        for anchor in current:
            anchor_total[anchor] += 1
            transition[anchor].update(following)
    values = {number: 0.0 for number in NUMBERS}
    baseline_count = max(len(subset) - 1, 1)
    for anchor in latest:
        support = anchor_total.get(anchor, 0)
        if support < 10:
            continue
        for target in NUMBERS:
            conditional = transition[anchor].get(target, 0) / support
            baseline = target_total.get(target, 0) / baseline_count
            lift = conditional - baseline
            if lift > 0:
                values[target] += lift
    return normalize(values)


def shape_follow_scores(draws: list[dict], window: int = 1500) -> dict[int, float]:
    subset = draws[-window:] if len(draws) > window else draws
    if len(subset) < 4:
        return {number: 0.0 for number in NUMBERS}
    latest_profile = draw_profile(draws[-1]["numbers"])
    values = {number: 0.0 for number in NUMBERS}
    similarities = []
    for index in range(len(subset) - 1):
        score = profile_similarity(latest_profile, draw_profile(subset[index]["numbers"]))
        if score >= 0.62:
            similarities.append((score, index))
    similarities.sort(reverse=True)
    for score, index in similarities[:80]:
        for number in subset[index + 1]["numbers"]:
            values[int(number)] += score
    return normalize(values)


def tail_zone_balance_scores(draws: list[dict]) -> dict[int, float]:
    recent = draws[-40:] if len(draws) >= 40 else draws
    tails = Counter(number % 10 for draw in recent for number in draw["numbers"])
    zones = Counter(zone_label(number) for draw in recent for number in draw["numbers"])
    tail_pressure = normalize_any({tail: 1.0 / max(tails.get(tail, 0), 1) for tail in range(10)})
    zone_pressure = normalize_any(
        {label: 1.0 / max(zones.get(label, 0), 1) for label in ("01-10", "11-20", "21-30", "31-39")}
    )
    latest = set(draws[-1]["numbers"])
    values = {}
    for number in NUMBERS:
        value = tail_pressure.get(number % 10, 0.0) * 0.52 + zone_pressure.get(zone_label(number), 0.0) * 0.48
        if any(abs(number - latest_number) == 1 for latest_number in latest):
            value += 0.08
        values[number] = value
    return normalize(values)


def sum_band_neighbor_scores(draws: list[dict], window: int = 1400) -> dict[int, float]:
    subset = draws[-window:] if len(draws) > window else draws
    if len(subset) < 4:
        return {number: 0.0 for number in NUMBERS}
    latest = draw_profile(draws[-1]["numbers"])
    values = {number: 0.0 for number in NUMBERS}
    for index in range(len(subset) - 1):
        profile = draw_profile(subset[index]["numbers"])
        sum_distance = abs(profile["sum"] - latest["sum"])
        span_distance = abs(profile["span"] - latest["span"])
        if sum_distance <= 18 or span_distance <= 8:
            score = max(0.0, 1.0 - sum_distance / 55) * 0.72 + max(0.0, 1.0 - span_distance / 28) * 0.28
            for number in subset[index + 1]["numbers"]:
                values[int(number)] += score
    return normalize(values)


def trend_break_scores(draws: list[dict]) -> dict[int, float]:
    fast = frequency(draws[-25:] if len(draws) >= 25 else draws)
    mid = frequency(draws[-120:] if len(draws) >= 120 else draws)
    slow = frequency(draws[-720:] if len(draws) >= 720 else draws)
    gaps = current_gaps(draws)
    values = {}
    for number in NUMBERS:
        f = fast.get(number, 0) / max(25, min(len(draws), 25))
        m = mid.get(number, 0) / max(120, min(len(draws), 120))
        s = slow.get(number, 0) / max(720, min(len(draws), 720))
        rebound = max(0.0, (m - s) + (f - m) * 0.45)
        pressure = min(1.0, gaps[number] / (SPEC.number_max / SPEC.draw_size * 2.2))
        values[number] = rebound + pressure * 0.38
    return normalize(values)


def normalize_number(value: int) -> int:
    value = abs(int(value))
    if value == 0:
        return SPEC.number_max
    return ((value - 1) % SPEC.number_max) + 1


def date_cycle_numbers(target_date: str) -> list[int]:
    current = datetime.strptime(target_date, "%Y-%m-%d").date()
    raw = [
        current.year,
        current.month,
        current.day,
        int(f"{current.month}{current.day:02d}"),
        current.timetuple().tm_yday,
        sum(int(char) for char in current.strftime("%Y%m%d")),
        current.month + current.day,
        (current.year - 1911) + current.month + current.day,
    ]
    result = []
    for value in raw:
        number = normalize_number(value)
        if number not in result:
            result.append(number)
    return result


def date_cycle_scores(target_date: str) -> dict[int, float]:
    cycle = set(date_cycle_numbers(target_date))
    return {number: 1.0 if number in cycle else 0.0 for number in NUMBERS}


def model_suite(draws: list[dict], target_date: str) -> dict[str, dict[int, float]]:
    return {
        "multi_window_frequency": multi_window_frequency_scores(draws),
        "omission_phase": omission_phase_scores(draws),
        "pair_lift": pair_lift_scores(draws),
        "shape_follow": shape_follow_scores(draws),
        "tail_zone_balance": tail_zone_balance_scores(draws),
        "sum_band_neighbor": sum_band_neighbor_scores(draws),
        "trend_break": trend_break_scores(draws),
        "date_cycle": date_cycle_scores(target_date),
    }


def combine_models(models: dict[str, dict[int, float]], weights: dict[str, float]) -> dict[int, float]:
    total_weight = sum(max(0.0, weights.get(name, 0.0)) for name in models) or 1.0
    values = {number: 0.0 for number in NUMBERS}
    for name, scores in models.items():
        weight = max(0.0, weights.get(name, 0.0)) / total_weight
        for number in NUMBERS:
            values[number] += scores.get(number, 0.0) * weight
    return normalize(values)


def model_backtest_weights(draws: list[dict], rounds: int = 90) -> tuple[dict[str, float], dict]:
    if len(draws) < 80:
        return dict(BASE_WEIGHTS), {"rounds": 0, "status": "history_too_short", "models": {}}
    start = max(50, len(draws) - rounds - 1)
    totals = {name: 0 for name in BASE_WEIGHTS}
    count = 0
    for index in range(start, len(draws) - 1):
        train = draws[: index + 1]
        actual = set(draws[index + 1]["numbers"])
        target_date = draws[index + 1]["draw_date"]
        models = model_suite(train, target_date)
        for name, scores in models.items():
            ranked = rank_values(scores)[:9]
            totals[name] += len(set(ranked) & actual)
        count += 1
    random_avg = RANDOM_TOP9
    adjusted = {}
    metrics = {}
    for name, base in BASE_WEIGHTS.items():
        avg_hits = totals[name] / count if count else 0
        edge = avg_hits - random_avg
        multiplier = 1.0 + max(-0.35, min(0.55, edge / max(random_avg, 1e-9)))
        adjusted[name] = max(0.025, base * multiplier)
        metrics[name] = {
            "label": MODEL_LABELS.get(name, name),
            "top9_avg_hits": round(avg_hits, 4),
            "edge_vs_random": round(edge, 4),
            "base_weight": base,
            "adjusted_weight": round(adjusted[name], 5),
        }
    total = sum(adjusted.values()) or 1.0
    weights = {name: round(value / total, 6) for name, value in adjusted.items()}
    return weights, {"rounds": count, "random_top9_expectation": round(random_avg, 4), "models": metrics}


def rolling_error_adjusted_weights(draws: list[dict], base_weights: dict[str, float], rounds: int) -> tuple[dict[str, float], dict]:
    if len(draws) < 80:
        return dict(base_weights), {
            "status": "history_too_short",
            "rule": "資料不足時不做錯誤模組懲罰，避免假調整。",
            "models": {},
        }
    test_rounds = max(max(ROLLING_REVIEW_WINDOWS), min(rounds, 180))
    start = max(50, len(draws) - test_rounds - 1)
    model_results = {name: [] for name in BASE_WEIGHTS}
    for index in range(start, len(draws) - 1):
        train = draws[: index + 1]
        actual = set(draws[index + 1]["numbers"])
        target_date = draws[index + 1]["draw_date"]
        models = model_suite(train, target_date)
        for name, scores in models.items():
            ranked = rank_values(scores)
            top5_hits = len(set(ranked[:5]) & actual)
            top9_hits = len(set(ranked[:9]) & actual)
            model_results[name].append(
                {
                    "top1_hit": 1 if ranked[0] in actual else 0,
                    "top5_hits": top5_hits,
                    "top9_hits": top9_hits,
                    "zero_top9": 1 if top9_hits == 0 else 0,
                }
            )

    expected_single = SPEC.draw_size / SPEC.number_max
    expected_top5 = SPEC.draw_size * 5 / SPEC.number_max
    expected_top9 = RANDOM_TOP9
    adjusted = {}
    audit_models = {}
    for name, weight in base_weights.items():
        rows = model_results.get(name, [])
        window_audits = {}
        corrections = []
        for window in ROLLING_REVIEW_WINDOWS:
            sample = rows[-window:]
            if not sample:
                continue
            top1_rate = sum(row["top1_hit"] for row in sample) / len(sample)
            top5_avg = sum(row["top5_hits"] for row in sample) / len(sample)
            top9_avg = sum(row["top9_hits"] for row in sample) / len(sample)
            zero_top9_rate = sum(row["zero_top9"] for row in sample) / len(sample)
            miss_streak = 0
            for row in reversed(sample):
                if row["top9_hits"] > 0:
                    break
                miss_streak += 1
            health = (
                clamp(top9_avg / expected_top9, 0.0, 1.8) * 0.42
                + clamp(top5_avg / expected_top5, 0.0, 1.8) * 0.26
                + clamp(top1_rate / expected_single, 0.0, 1.8) * 0.22
                + (1.0 - zero_top9_rate) * 0.10
            )
            correction = clamp(0.52 + health * 0.48, 0.45, 1.65)
            correction *= 0.94 ** min(miss_streak, 6)
            correction = clamp(correction, 0.40, 1.65)
            corrections.append(correction)
            window_audits[str(window)] = {
                "top1_hit_rate": round(top1_rate, 4),
                "top5_avg_hits": round(top5_avg, 4),
                "top9_avg_hits": round(top9_avg, 4),
                "zero_top9_rate": round(zero_top9_rate, 4),
                "miss_streak": miss_streak,
                "correction": round(correction, 4),
            }
        if corrections:
            weights_for_windows = [0.5, 0.3, 0.2][: len(corrections)]
            correction = sum(value * w for value, w in zip(corrections, weights_for_windows)) / sum(weights_for_windows)
        else:
            correction = 1.0
        adjusted[name] = max(0.015, weight * correction)
        if correction < 0.92:
            action = "錯誤模組降權後重算"
        elif correction > 1.08:
            action = "有效模組升權後重算"
        else:
            action = "維持權重但重新運算"
        audit_models[name] = {
            "label": MODEL_LABELS.get(name, name),
            "input_weight": round(weight, 6),
            "correction": round(correction, 4),
            "output_weight_before_normalize": round(adjusted[name], 6),
            "action": action,
            "windows": window_audits,
        }
    total = sum(adjusted.values()) or 1.0
    final_weights = {name: round(value / total, 6) for name, value in adjusted.items()}
    for name, value in final_weights.items():
        audit_models[name]["final_weight"] = value
    failed = [name for name, item in audit_models.items() if item["correction"] < 0.92]
    boosted = [name for name, item in audit_models.items() if item["correction"] > 1.08]
    return final_weights, {
        "status": "applied",
        "review_rounds": len(next(iter(model_results.values()), [])),
        "windows": list(ROLLING_REVIEW_WINDOWS),
        "failed_models_reweighted": failed,
        "boosted_models_reweighted": boosted,
        "rule": "每期結算後依12/30/90期滾動命中、零命中與獨隻命中率重新調整所有模型權重。",
        "models": audit_models,
    }


def low_hit_regime_review(history: list[dict] | None) -> dict:
    rows = list(history or [])
    window_metrics = {}
    for window in LOW_HIT_REVIEW_WINDOWS:
        sample = rows[:window]
        if not sample:
            window_metrics[str(window)] = {
                "status": "no_settled_history",
                "sample_size": 0,
                "top9_avg_hits": None,
                "top15_avg_hits": None,
                "zero_top9_rate": None,
                "strong_single_hit_rate": None,
            }
            continue
        top9_values = [int(item.get("top9_hits") or 0) for item in sample]
        top15_values = [int(item.get("top15_hits") or 0) for item in sample]
        single_values = []
        for item in sample:
            pack_hits = item.get("strong_pack_hits") or {}
            single = pack_hits.get("strong_single") or {}
            single_values.append(1 if int(single.get("hits") or 0) >= 1 else 0)
        window_metrics[str(window)] = {
            "status": "reviewed",
            "sample_size": len(sample),
            "top9_avg_hits": round(sum(top9_values) / len(sample), 4),
            "top15_avg_hits": round(sum(top15_values) / len(sample), 4),
            "zero_top9_rate": round(sum(1 for value in top9_values if value == 0) / len(sample), 4),
            "strong_single_hit_rate": round(sum(single_values) / len(sample), 4),
        }

    basis = next((window_metrics[str(window)] for window in LOW_HIT_REVIEW_WINDOWS if window_metrics[str(window)]["sample_size"]), None)
    if not basis:
        return {
            "status": "no_settled_history",
            "mode": "standard",
            "severity": 0.0,
            "windows": window_metrics,
            "rule": "尚無足夠已結算預測，不啟動低命中權重轉換。",
        }
    top9_avg = float(basis.get("top9_avg_hits") or 0.0)
    zero_rate = float(basis.get("zero_top9_rate") or 0.0)
    single_rate = float(basis.get("strong_single_hit_rate") or 0.0)
    top9_deficit = clamp((RANDOM_TOP9 - top9_avg) / max(RANDOM_TOP9, 1e-9), 0.0, 1.0)
    zero_pressure = clamp((zero_rate - 0.18) / 0.62, 0.0, 1.0)
    single_pressure = clamp((0.18 - single_rate) / 0.18, 0.0, 1.0)
    severity = round(clamp(top9_deficit * 0.52 + zero_pressure * 0.30 + single_pressure * 0.18, 0.0, 1.0), 4)
    if severity >= 0.62:
        mode = "low_hit_recovery"
        status = "critical_shift"
    elif severity >= 0.34:
        mode = "guarded_recovery"
        status = "watch_shift"
    else:
        mode = "standard"
        status = "normal"
    return {
        "status": status,
        "mode": mode,
        "severity": severity,
        "basis_window": basis.get("sample_size", 0),
        "random_top9_expectation": round(RANDOM_TOP9, 4),
        "windows": window_metrics,
        "rule": "最近命中低於隨機基準或零命中率偏高時，切換為低命中回復模式：降權近期落空來源，升權漏抓回補、拖牌轉折與牌型跟隨。",
    }


def failure_memory_from_settled(history: list[dict] | None, limit: int = 20) -> dict:
    rows = list(history or [])[:limit]
    if not rows:
        return {"status": "inactive", "sample_size": 0, "numbers": {}, "rule": "無已結算資料可回灌。"}
    selected = Counter()
    missed = Counter()
    hit = Counter()
    leak = Counter()
    second_layer_hit = Counter()
    single_miss = Counter()
    for item in rows:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        top9 = candidates[:9]
        top15 = candidates[:15]
        for number in top9:
            selected[number] += 1
            if number in actual:
                hit[number] += 1
            else:
                missed[number] += 1
        for number in sorted(actual - set(top9)):
            leak[number] += 1
        for number in sorted(set(top15[9:15]) & actual):
            second_layer_hit[number] += 1
        strong_single = (((item.get("strong_packs") or {}).get("strong_single") or {}).get("numbers") or [])[:1]
        for number in strong_single:
            number = int(number)
            if number not in actual:
                single_miss[number] += 1

    recovery_raw = {
        number: leak.get(number, 0) * 1.0 + second_layer_hit.get(number, 0) * 0.72 + hit.get(number, 0) * 0.18
        for number in NUMBERS
    }
    penalty_raw = {
        number: missed.get(number, 0) * 0.72 + single_miss.get(number, 0) * 0.55 - hit.get(number, 0) * 0.28
        for number in NUMBERS
    }
    recovery_norm = normalize_any(recovery_raw)
    penalty_norm = normalize_any({number: max(0.0, value) for number, value in penalty_raw.items()})
    numbers = {}
    for number in NUMBERS:
        numbers[number] = {
            "recovery_score": round(float(recovery_norm.get(number, 0.0)), 4),
            "miss_penalty": round(float(penalty_norm.get(number, 0.0)), 4),
            "leak_count": int(leak.get(number, 0)),
            "top9_miss_count": int(missed.get(number, 0)),
            "top9_hit_count": int(hit.get(number, 0)),
            "second_layer_hit_count": int(second_layer_hit.get(number, 0)),
        }
    return {
        "status": "active",
        "sample_size": len(rows),
        "top_leak_numbers": [number for number, _ in leak.most_common(10)],
        "top_penalty_numbers": [number for number, _ in missed.most_common(10)],
        "second_layer_hit_numbers": [number for number, _ in second_layer_hit.most_common(10)],
        "numbers": numbers,
        "rule": "以已結算預測建立實戰記憶：前九漏抓的實開號加回補分，前九多次落空與獨隻落空號降權。",
    }


def front9_escape_review(history: list[dict] | None, limit: int = FRONT9_ESCAPE_REVIEW_LIMIT) -> dict:
    rows = [item for item in list(history or [])[:limit] if item.get("actual_numbers") and item.get("candidates")]
    if not rows:
        return {
            "status": "inactive",
            "sample_size": 0,
            "numbers": {},
            "rule": "尚無已結算資料，暫不啟動第10到15名外溢校正。",
        }

    top9_total = 0
    top15_total = 0
    second_layer_extra_total = 0
    second_layer_escape_periods = 0
    second_layer_hit = Counter()
    second_layer_selected = Counter()
    leak = Counter()
    top9_hit = Counter()
    top9_miss = Counter()
    escape_period_rows = []

    for item in rows:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        top9 = candidates[:9]
        second_layer = candidates[9:15]
        top15 = candidates[:15]
        top9_hits = sorted(set(top9) & actual)
        second_hits = sorted(set(second_layer) & actual)
        top9_total += len(top9_hits)
        top15_total += len(set(top15) & actual)
        second_layer_extra_total += len(second_hits)
        if second_hits:
            second_layer_escape_periods += 1
            escape_period_rows.append(
                {
                    "actual_date": item.get("actual_date"),
                    "second_layer_hits": second_hits,
                    "top9_hits": top9_hits,
                }
            )
        for number in top9:
            if number in actual:
                top9_hit[number] += 1
            else:
                top9_miss[number] += 1
        for number in second_layer:
            second_layer_selected[number] += 1
        for number in second_hits:
            second_layer_hit[number] += 1
        for number in sorted(actual - set(top9)):
            leak[number] += 1

    raw_scores = {}
    for number in NUMBERS:
        raw_scores[number] = max(
            0.0,
            second_layer_hit.get(number, 0) * 1.45
            + leak.get(number, 0) * 0.88
            + second_layer_selected.get(number, 0) * 0.12
            + top9_hit.get(number, 0) * 0.16
            - top9_miss.get(number, 0) * 0.20,
        )
    normalized = normalize_any(raw_scores)
    numbers = {
        number: {
            "rank_escape_score": round(float(normalized.get(number, 0.0)), 4),
            "second_layer_hit_count": int(second_layer_hit.get(number, 0)),
            "second_layer_selected_count": int(second_layer_selected.get(number, 0)),
            "leak_count": int(leak.get(number, 0)),
            "top9_hit_count": int(top9_hit.get(number, 0)),
            "top9_miss_count": int(top9_miss.get(number, 0)),
        }
        for number in NUMBERS
    }
    top_escape_numbers = [
        number
        for number, _ in sorted(
            raw_scores.items(),
            key=lambda item: (item[1], second_layer_hit.get(item[0], 0), leak.get(item[0], 0), -item[0]),
            reverse=True,
        )[:12]
        if raw_scores.get(number, 0.0) > 0
    ]
    return {
        "status": "active",
        "sample_size": len(rows),
        "top9_avg_hits": round(top9_total / len(rows), 4),
        "top15_avg_hits": round(top15_total / len(rows), 4),
        "second_layer_extra_hits_total": int(second_layer_extra_total),
        "second_layer_escape_periods": int(second_layer_escape_periods),
        "second_layer_escape_rate": round(second_layer_escape_periods / len(rows), 4),
        "top_escape_numbers": top_escape_numbers,
        "recent_escape_periods": escape_period_rows[:8],
        "numbers": numbers,
        "rule": "每期檢查命中是否掉到第10到15名；若有外溢，下一期前九尾端弱號必須與外溢強訊號重排。",
    }


def apply_front9_escape_correction(
    candidates: list[dict],
    failure_memory: dict | None,
    settled_history_rows: list[dict] | None,
) -> tuple[list[dict], dict]:
    review = front9_escape_review(settled_history_rows)
    memory_numbers = (failure_memory or {}).get("numbers") or {}
    rows = []
    for item in candidates:
        cloned = dict(item)
        cloned["reasons"] = list(item.get("reasons") or [])
        rows.append(cloned)

    original_top9 = [int(item["number"]) for item in rows[:9]]
    original_second_layer = [int(item["number"]) for item in rows[9:15]]

    def safe_score(item: dict) -> float:
        try:
            return float(item.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    if review.get("status") != "active" or len(rows) < 10:
        for rank, item in enumerate(rows, 1):
            item["rank"] = rank
            item["front9_escape_score"] = 0.0
            item["front9_escape_status"] = "尚無外溢校正"
            item["front9_original_rank"] = rank
        audit = {
            "status": "inactive",
            "review": review,
            "previous_top9": original_top9,
            "current_second_layer_before": original_second_layer,
            "corrected_top9": original_top9,
            "promoted_numbers": [],
            "demoted_numbers": [],
            "swaps": [],
            "rule": review.get("rule"),
        }
        return rows, audit

    review_numbers = review.get("numbers") or {}
    escape_pressure = float(review.get("second_layer_escape_rate") or 0.0)
    extra_total = int(review.get("second_layer_extra_hits_total") or 0)
    pressure_factor = clamp(0.35 + escape_pressure, 0.0, 1.0)

    for rank, item in enumerate(rows, 1):
        number = int(item["number"])
        memory = memory_numbers.get(number) or {}
        rank_memory = review_numbers.get(number) or {}
        recovery_score = float(memory.get("recovery_score") or 0.0)
        rank_escape_score = float(rank_memory.get("rank_escape_score") or 0.0)
        repeated_second_hit = float(memory.get("second_layer_hit_count") or 0.0)
        leak_count = float(memory.get("leak_count") or 0.0)
        band_pressure = 0.0
        if 10 <= rank <= 15 and extra_total:
            band_pressure = clamp((16 - rank) / 6.0, 0.0, 1.0) * 0.38 * pressure_factor
        escape_score = max(
            rank_escape_score,
            recovery_score * 0.72 + min(0.30, repeated_second_hit * 0.12) + min(0.18, leak_count * 0.06),
            band_pressure,
        )
        item["front9_original_rank"] = rank
        item["front9_escape_score"] = round(float(escape_score), 4)
        item["front9_escape_status"] = (
            "前九守門"
            if rank <= 9
            else ("第10到15名外溢候選" if rank <= 15 and escape_score > 0 else "後段候選")
        )
        item["second_layer_hit_count"] = int(memory.get("second_layer_hit_count") or rank_memory.get("second_layer_hit_count") or 0)
        item["leak_count"] = int(memory.get("leak_count") or rank_memory.get("leak_count") or 0)
        item["top9_miss_count"] = int(memory.get("top9_miss_count") or rank_memory.get("top9_miss_count") or 0)
        item["top9_hit_count"] = int(memory.get("top9_hit_count") or rank_memory.get("top9_hit_count") or 0)

    def holder_weakness(item: dict) -> float:
        number = int(item["number"])
        memory = memory_numbers.get(number) or {}
        rank_memory = review_numbers.get(number) or {}
        original_rank = int(item.get("front9_original_rank") or item.get("rank") or 9)
        miss_penalty = float(memory.get("miss_penalty") or 0.0)
        top9_miss = float(memory.get("top9_miss_count") or rank_memory.get("top9_miss_count") or 0.0)
        top9_hit = float(memory.get("top9_hit_count") or rank_memory.get("top9_hit_count") or 0.0)
        tail_pressure = clamp((original_rank - 5) / 4.0, 0.0, 1.0) * 0.24
        return clamp((1.0 - safe_score(item)) * 0.18 + miss_penalty * 0.55 + min(0.32, top9_miss * 0.08) - min(0.18, top9_hit * 0.06) + tail_pressure, 0.0, 1.0)

    def challenger_strength(item: dict) -> float:
        number = int(item["number"])
        memory = memory_numbers.get(number) or {}
        rank_memory = review_numbers.get(number) or {}
        recovery = float(memory.get("recovery_score") or 0.0)
        second_hits = float(memory.get("second_layer_hit_count") or rank_memory.get("second_layer_hit_count") or 0.0)
        support = float(item.get("support_models") or 0.0)
        return safe_score(item) + float(item.get("front9_escape_score") or 0.0) * 0.16 + recovery * 0.05 + min(0.06, second_hits * 0.025) + support * 0.003

    top_pool = rows[:9]
    second_pool = rows[9:15]
    tail_pool = rows[15:]
    max_swaps = 3 if extra_total >= 3 or escape_pressure >= 0.34 else (2 if extra_total else 1)
    challenger_pool = [
        item
        for item in second_pool
        if float(item.get("front9_escape_score") or 0.0) >= 0.20 or extra_total
    ]
    challenger_pool.sort(
        key=lambda item: (challenger_strength(item), float(item.get("front9_escape_score") or 0.0), safe_score(item), -int(item["number"])),
        reverse=True,
    )
    swaps = []
    for challenger in challenger_pool:
        if len(swaps) >= max_swaps or challenger not in second_pool:
            continue
        holder_candidates = [item for item in top_pool if item.get("front9_escape_status") != "已拉回前九"] or top_pool
        holder = max(
            holder_candidates,
            key=lambda item: (holder_weakness(item), -safe_score(item), int(item.get("front9_original_rank") or 0)),
        )
        ch_adjusted = challenger_strength(challenger)
        holder_weakness_value = holder_weakness(holder)
        holder_adjusted = safe_score(holder) - holder_weakness_value * 0.07
        force_due_to_escape = extra_total > 0 and float(challenger.get("front9_escape_score") or 0.0) >= 0.24
        if not force_due_to_escape and ch_adjusted < holder_adjusted * 0.985:
            continue

        challenger_old_score = safe_score(challenger)
        top_pool.remove(holder)
        second_pool.remove(challenger)
        top_pool.append(challenger)
        second_pool.append(holder)
        challenger["front9_escape_status"] = "已拉回前九"
        holder["front9_escape_status"] = "前九尾端降到備查"
        challenger["reasons"].append("第10到15名外溢校正拉回前九")
        holder["reasons"].append("前九外溢檢討後降到備查")
        challenger["score"] = round(min(1.0, safe_score(challenger) + float(challenger.get("front9_escape_score") or 0.0) * 0.025 + 0.012), 6)
        holder["score"] = round(
            max(
                0.0,
                min(
                    safe_score(holder) - 0.09 - holder_weakness_value * 0.035,
                    challenger_old_score - 0.008,
                ),
            ),
            6,
        )
        swaps.append(
            {
                "promoted": int(challenger["number"]),
                "demoted": int(holder["number"]),
                "promoted_original_rank": int(challenger.get("front9_original_rank") or 0),
                "demoted_original_rank": int(holder.get("front9_original_rank") or 0),
                "escape_score": round(float(challenger.get("front9_escape_score") or 0.0), 4),
                "holder_weakness": round(holder_weakness_value, 4),
                "rule": "第10到15名外溢訊號高於前九尾端保留強度，執行交換。",
            }
        )

    top_pool.sort(
        key=lambda item: (safe_score(item), float(item.get("front9_escape_score") or 0.0), float(item.get("confidence_index") or 0.0), -int(item["number"])),
        reverse=True,
    )
    rest_pool = second_pool + tail_pool
    rest_pool.sort(
        key=lambda item: (safe_score(item), float(item.get("front9_escape_score") or 0.0), float(item.get("confidence_index") or 0.0), -int(item["number"])),
        reverse=True,
    )
    corrected = top_pool + rest_pool
    for rank, item in enumerate(corrected, 1):
        item["rank"] = rank
        item["front9_final_layer"] = "前九核心" if rank <= 9 else ("第10到15名備查" if rank <= 15 else "後段觀察")
    corrected_top9 = [int(item["number"]) for item in corrected[:9]]
    net_promoted = [number for number in corrected_top9 if number in set(original_second_layer)]
    net_demoted = [number for number in original_top9 if number not in set(corrected_top9)]

    audit = {
        "status": "applied" if swaps else "reviewed_no_swap",
        "review": {key: value for key, value in review.items() if key != "numbers"},
        "previous_top9": original_top9,
        "current_second_layer_before": original_second_layer,
        "corrected_top9": corrected_top9,
        "promoted_numbers": net_promoted,
        "demoted_numbers": net_demoted,
        "swap_count": len(net_promoted),
        "swaps": swaps,
        "rule": "第10到15名補中造成前九失準時，下一期立即壓縮到前九內，目標把主要候選鎖在9顆內。",
    }
    return corrected, audit


def apply_low_hit_regime_shift(weights: dict[str, float], review: dict) -> tuple[dict[str, float], dict]:
    severity = float(review.get("severity") or 0.0)
    if severity <= 0:
        review["weight_transform"] = {"status": "not_applied", "model_multipliers": {}}
        return dict(weights), review
    multipliers = {name: 1.0 for name in weights}
    multipliers["multi_window_frequency"] *= 1.0 - 0.22 * severity
    multipliers["date_cycle"] *= 1.0 - 0.20 * severity
    multipliers["tail_zone_balance"] *= 1.0 - 0.10 * severity
    multipliers["omission_phase"] *= 1.0 + 0.18 * severity
    multipliers["pair_lift"] *= 1.0 + 0.22 * severity
    multipliers["shape_follow"] *= 1.0 + 0.20 * severity
    multipliers["trend_break"] *= 1.0 + 0.16 * severity
    adjusted = {
        name: max(0.01, float(value) * multipliers.get(name, 1.0))
        for name, value in weights.items()
    }
    total = sum(adjusted.values()) or 1.0
    final = {name: round(value / total, 6) for name, value in adjusted.items()}
    review["weight_transform"] = {
        "status": "applied",
        "severity": round(severity, 4),
        "model_multipliers": {name: round(value, 4) for name, value in multipliers.items()},
        "before_weights": {name: round(float(value), 6) for name, value in weights.items()},
        "after_weights": final,
        "rule": "低命中時降低純頻率與日期輔助，提升拖牌、牌型、遺漏與趨勢轉折，避免同一批失準來源繼續主導。",
    }
    return final, review


def candidate_reasons(number: int, models: dict[str, dict[int, float]], limit: int = 5) -> list[str]:
    support = []
    for name, scores in models.items():
        ranked = rank_values(scores)
        rank = ranked.index(number) + 1 if number in ranked else 99
        score = scores.get(number, 0.0)
        if score >= 0.62 or rank <= 5:
            support.append((score, -rank, MODEL_LABELS.get(name, name)))
    support.sort(reverse=True)
    return [label for _, _, label in support[:limit]] or ["綜合模型"]


def score_numbers(draws: list[dict], weights: dict[str, float] | None = None, failure_memory: dict | None = None) -> dict:
    target_date = next_draw_date(draws[-1]["draw_date"])
    models = model_suite(draws, target_date)
    active_weights = weights or dict(BASE_WEIGHTS)
    ensemble = combine_models(models, active_weights)
    latest_numbers = set(draws[-1]["numbers"])
    gaps = current_gaps(draws)

    guarded = dict(ensemble)
    memory_numbers = (failure_memory or {}).get("numbers") or {}
    memory_active = (failure_memory or {}).get("status") == "active"
    for number in latest_numbers:
        guarded[number] = max(0.0, guarded[number] - 0.18)
    if memory_active:
        for number in NUMBERS:
            memory = memory_numbers.get(number) or {}
            recovery = float(memory.get("recovery_score") or 0.0)
            penalty = float(memory.get("miss_penalty") or 0.0)
            guarded[number] = max(0.0, guarded[number] * (1.0 + recovery * 0.22 - penalty * 0.18) + recovery * 0.035)
    guarded = normalize(guarded)

    rows = []
    for number in NUMBERS:
        confidence = 50 + guarded[number] * 49
        support_count = sum(1 for scores in models.values() if scores.get(number, 0.0) >= 0.62)
        reasons = candidate_reasons(number, models)
        last_draw_repeat = number in latest_numbers
        memory = memory_numbers.get(number) or {}
        recovery_score = float(memory.get("recovery_score") or 0.0)
        miss_penalty = float(memory.get("miss_penalty") or 0.0)
        if recovery_score >= 0.55:
            reasons.append("低命中漏抓回補")
        if miss_penalty >= 0.65:
            reasons.append("近期多次落空降權後仍保留觀察")
        if last_draw_repeat:
            reasons.append("最新開獎號降權；禁止作為本期獨隻")
        rows.append(
            {
                "number": number,
                "score": round(guarded[number], 6),
                "confidence_index": round(confidence, 1),
                "model_probability_index": round(max(1.0, min(28.0, (confidence - 50) / 49 * 25)), 2),
                "omission": gaps[number],
                "zone": zone_label(number),
                "support_models": support_count,
                "reasons": reasons,
                "last_draw_repeat": last_draw_repeat,
                "low_hit_recovery_score": round(recovery_score, 4),
                "recent_miss_penalty": round(miss_penalty, 4),
                "front9_escape_score": 0.0,
                "front9_escape_status": "待外溢檢查",
                "second_layer_hit_count": int(memory.get("second_layer_hit_count") or 0),
                "leak_count": int(memory.get("leak_count") or 0),
                "top9_miss_count": int(memory.get("top9_miss_count") or 0),
                "top9_hit_count": int(memory.get("top9_hit_count") or 0),
                "strong_single_eligible": not last_draw_repeat,
                "strict_guard": "最新開獎號不得直接作為本期獨隻" if last_draw_repeat else "通過非最新開獎號獨隻守門",
                "model_scores": {name: round(scores.get(number, 0.0), 4) for name, scores in models.items()},
            }
        )
    rows.sort(key=lambda item: (item["score"], item["confidence_index"], -item["number"]), reverse=True)
    for rank, item in enumerate(rows, 1):
        item["rank"] = rank
    return {"candidates": rows, "models": models, "weights": active_weights, "target_date": target_date}


def combinations_count(n: int, r: int) -> int:
    if r < 0 or r > n:
        return 0
    return math.comb(n, r)


def theoretical_probability(pool_size: int, hit_goal: int) -> dict:
    total = combinations_count(SPEC.number_max, SPEC.draw_size)
    favorable = 0
    for hits in range(hit_goal, min(pool_size, SPEC.draw_size) + 1):
        favorable += combinations_count(pool_size, hits) * combinations_count(
            SPEC.number_max - pool_size, SPEC.draw_size - hits
        )
    probability = favorable / total if total else 0.0
    return {
        "probability": round(probability, 8),
        "odds_1_in": round(1 / probability, 2) if probability else None,
        "random_expected_hits": round(SPEC.draw_size * pool_size / SPEC.number_max, 4),
    }


def coverage_score(numbers: list[int]) -> float:
    zones = Counter(zone_label(number) for number in numbers)
    tails = Counter(number % 10 for number in numbers)
    zone_collision = sum(max(0, count - 3) for count in zones.values())
    tail_collision = sum(max(0, count - 1) for count in tails.values())
    span = max(numbers) - min(numbers) if numbers else 0
    span_score = min(1.0, span / 28)
    return max(0.0, 0.72 + span_score * 0.18 - zone_collision * 0.08 - tail_collision * 0.035)


def combo_quality(numbers: list[int], candidate_map: dict[int, dict]) -> float:
    scores = [candidate_map[number]["score"] for number in numbers]
    confidence = sum(scores) / len(scores)
    floor = min(scores)
    return confidence * 0.72 + floor * 0.18 + coverage_score(numbers) * 0.10


def best_pack(candidates: list[dict], size: int) -> list[int]:
    candidate_map = {item["number"]: item for item in candidates}
    pool_size = min(len(candidates), 14 if size <= 5 else 15)
    pool = [item["number"] for item in candidates[:pool_size]]
    if len(pool) <= size:
        return sorted(pool)
    best = max(
        combinations(pool, size),
        key=lambda combo: (combo_quality(list(combo), candidate_map), sum(candidate_map[number]["score"] for number in combo)),
    )
    return sorted(best)


def select_strong_single(candidates: list[dict]) -> tuple[list[int], dict]:
    eligible = [item for item in candidates if item.get("strong_single_eligible", True)]
    selected = eligible[0] if eligible else (candidates[0] if candidates else None)
    if not selected:
        return [], {"status": "blocked", "reason": "沒有候選號可選"}
    return [int(selected["number"])], {
        "status": "passed" if selected in eligible else "fallback",
        "selected_rank": selected.get("rank"),
        "selected_score": selected.get("score"),
        "selected_confidence": selected.get("confidence_index"),
        "last_draw_repeat": bool(selected.get("last_draw_repeat")),
        "rule": "獨隻1中1優先排除最新開獎號，禁止用上期開獎號混充。",
    }


def build_packs(candidates: list[dict]) -> dict:
    nums = [item["number"] for item in candidates]
    single_numbers, single_audit = select_strong_single(candidates)
    specs = [
        ("strong_single", "最強獨隻1中1", single_numbers, 1),
        ("two_hit_one", "最強2中1", nums[:2], 1),
        ("three_hit_one", "最強3中1", nums[:3], 1),
        ("five_hit_two", "最強5中2", best_pack(candidates, 5), 2),
        ("nine_hit_three", "最強9中3", best_pack(candidates, 9), 3),
    ]
    packs = {}
    for key, name, numbers, goal in specs:
        packs[key] = {
            "name": name,
            "numbers": sorted(numbers),
            "hit_goal": goal,
            "theoretical_probability": theoretical_probability(len(numbers), goal),
            "coverage_score": round(coverage_score(sorted(numbers)), 4) if numbers else 0,
        }
        if key == "strong_single":
            packs[key]["selection_audit"] = single_audit
    return packs


def backtest(draws: list[dict], rounds: int, weights: dict[str, float]) -> dict:
    if len(draws) < 80:
        return {"rounds": 0, "status": "history_too_short"}
    start = max(50, len(draws) - rounds - 1)
    totals = Counter()
    pack_hits = defaultdict(list)
    count = 0
    for index in range(start, len(draws) - 1):
        train = draws[: index + 1]
        actual = set(draws[index + 1]["numbers"])
        scored = score_numbers(train, weights)
        ranked = [item["number"] for item in scored["candidates"]]
        totals["top5"] += len(set(ranked[:5]) & actual)
        totals["top9"] += len(set(ranked[:9]) & actual)
        totals["top10"] += len(set(ranked[:10]) & actual)
        totals["top15"] += len(set(ranked[:15]) & actual)
        for key, pack in build_packs(scored["candidates"]).items():
            hits = len(set(pack["numbers"]) & actual)
            pack_hits[key].append(hits)
        count += 1
    if not count:
        return {"rounds": 0, "status": "not_enough_test_rows"}
    pack_summary = {}
    for key, values in pack_hits.items():
        goal = {"strong_single": 1, "two_hit_one": 1, "three_hit_one": 1, "five_hit_two": 2, "nine_hit_three": 3}[key]
        pack_summary[key] = {
            "avg_hits": round(sum(values) / len(values), 4),
            "pass_rate": round(sum(1 for value in values if value >= goal) / len(values), 4),
            "zero_rate": round(sum(1 for value in values if value == 0) / len(values), 4),
            "hit_goal": goal,
        }
    random = {
        "top5": SPEC.draw_size * 5 / SPEC.number_max,
        "top9": SPEC.draw_size * 9 / SPEC.number_max,
        "top10": SPEC.draw_size * 10 / SPEC.number_max,
        "top15": SPEC.draw_size * 15 / SPEC.number_max,
    }
    result = {
        "rounds": count,
        "top5_avg_hits": round(totals["top5"] / count, 4),
        "top9_avg_hits": round(totals["top9"] / count, 4),
        "top10_avg_hits": round(totals["top10"] / count, 4),
        "top15_avg_hits": round(totals["top15"] / count, 4),
        "random_expectation": {key: round(value, 4) for key, value in random.items()},
        "pack_summary": pack_summary,
    }
    result["top9_edge_vs_random"] = round(result["top9_avg_hits"] - random["top9"], 4)
    result["top10_edge_vs_random"] = round(result["top10_avg_hits"] - random["top10"], 4)
    result["status"] = "research_positive" if result["top9_edge_vs_random"] > 0 else "research_watch"
    return result


def load_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def history_metadata() -> dict:
    summary = load_json_object(FETCH_SUMMARY_JSON)
    audit = load_json_object(HISTORY_GAP_AUDIT_JSON)
    compact_audit = summary.get("history_gap_audit") if isinstance(summary.get("history_gap_audit"), dict) else {}
    return {
        "fetch_summary": summary,
        "gap_audit": audit or compact_audit,
    }


def history_completeness(draw_count: int, metadata: dict | None = None) -> dict:
    required = 3000
    base_status = "complete" if draw_count >= required else ("partial" if draw_count >= 180 else "seed_only")
    metadata = metadata or {}
    fetch_summary = metadata.get("fetch_summary") if isinstance(metadata.get("fetch_summary"), dict) else {}
    gap_audit = metadata.get("gap_audit") if isinstance(metadata.get("gap_audit"), dict) else {}
    earliest = fetch_summary.get("earliest_draw_date")
    latest = fetch_summary.get("latest_draw_date")
    official_range = gap_audit.get("official_public_range") or (f"{earliest}..{latest}" if earliest and latest else "")
    draw_gap = gap_audit.get("draw_number_gap_summary") if isinstance(gap_audit.get("draw_number_gap_summary"), dict) else {}
    missing_before = int(draw_gap.get("minimum_missing_before_public_range") or 0)
    prehistory_status = gap_audit.get("prehistory_status") or "not_scanned"
    status = "official_public_partial" if base_status != "complete" and official_range else base_status
    note = "完整回測建議至少3000期；不足時只能列研究觀察。"
    if official_range:
        note = (
            f"NLA官方公開接口目前可驗證範圍：{official_range}；"
            f"2000-01-01..2024-03-31 掃描狀態：{prehistory_status}；"
            f"依官方期號序列推估公開起點前至少缺 {missing_before} 期。"
        )
    return {
        "status": status,
        "required_minimum": required,
        "current_count": draw_count,
        "date_range": official_range,
        "official_public_range": official_range,
        "earliest_official_draw_date": earliest,
        "latest_official_draw_date": latest,
        "prehistory_audit_range": gap_audit.get("prehistory_audit_range"),
        "prehistory_status": prehistory_status,
        "prehistory_direct_rows": gap_audit.get("prehistory_direct_rows"),
        "minimum_missing_before_public_range": missing_before,
        "gap_audit_json": str(HISTORY_GAP_AUDIT_JSON),
        "note": note,
    }


def data_integrity_gate(draws: list[dict], metadata: dict | None = None) -> dict:
    issues = []
    seen_dates = set()
    previous = None
    official_source_count = 0
    for draw in draws:
        draw_date = str(draw.get("draw_date", ""))
        numbers = draw.get("numbers", [])
        source = str(draw.get("source", ""))
        if draw_date in seen_dates:
            issues.append(f"duplicate_date:{draw_date}")
        seen_dates.add(draw_date)
        if previous and draw_date <= previous:
            issues.append(f"date_order:{draw_date}")
        previous = draw_date
        if not valid_numbers(numbers):
            issues.append(f"invalid_numbers:{draw_date}")
        if not source:
            issues.append(f"missing_source:{draw_date}")
        if "NLA official" in source or "winning-numbers" in source:
            official_source_count += 1
    metadata = metadata or {}
    fetch_summary = metadata.get("fetch_summary") if isinstance(metadata.get("fetch_summary"), dict) else {}
    official_latest = fetch_summary.get("latest_draw_date")
    if official_latest and draws and official_latest != draws[-1]["draw_date"]:
        issues.append(f"latest_mismatch:{official_latest}!={draws[-1]['draw_date']}")
    status = "passed" if not issues else "blocked"
    return {
        "status": status,
        "draw_count": len(draws),
        "official_source_count": official_source_count,
        "issues": issues[:30],
        "rule": "禁止假資料、空來源、重複日期、錯誤號碼與官方最新日期不一致時產生正式高信心。",
    }


def freshness(latest_draw_date: str, target_date: str) -> dict:
    latest = datetime.strptime(latest_draw_date, "%Y-%m-%d").date()
    draw_today = now_draw_timezone().date()
    age_days = (draw_today - latest).days
    status = "fresh" if age_days <= 1 else ("watch" if age_days <= 3 else "stale")
    return {
        "status": status,
        "draw_timezone_today": draw_today.isoformat(),
        "taiwan_today": now_taiwan().date().isoformat(),
        "latest_draw_date": latest_draw_date,
        "age_days": age_days,
        "target_draw_date": target_date,
        "target_taiwan_safe_update_time": target_taiwan_safe_time(target_date),
        "daily_draw_time_taiwan": SPEC.draw_time_taiwan,
    }


def release_gate(backtest_result: dict, completeness: dict, fresh: dict) -> dict:
    if completeness["status"] != "complete":
        status = "research_only"
        reason = "歷史資料不足，僅列研究觀察。"
    elif fresh["status"] == "stale":
        status = "freshness_blocked"
        reason = "資料落後過久，禁止包裝成正式高信心。"
    elif backtest_result.get("top9_edge_vs_random", 0) >= 0.08 and backtest_result.get("rounds", 0) >= 90:
        status = "official_watch"
        reason = "回測高於隨機基準，可列正式觀察；仍不可保證命中。"
    else:
        status = "watch_only"
        reason = "未達正式發布門檻，只列觀察。"
    return {
        "status": status,
        "reason": reason,
        "rule": "只有資料完整、新鮮度正常、且Top9回測明顯高於隨機基準時，才允許升級為正式觀察。",
    }


def latest_settled(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT based_on_date,actual_date,actual_numbers_json,candidates_json,strong_packs_json,
               top5_hits,top9_hits,top10_hits,top15_hits,strong_pack_hits_json
        FROM predictions
        WHERE status='settled'
        ORDER BY actual_date DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return {}
    return {
        "based_on_date": row[0],
        "actual_date": row[1],
        "actual_numbers": json.loads(row[2] or "[]"),
        "candidates": json.loads(row[3] or "[]"),
        "strong_packs": json.loads(row[4] or "{}"),
        "top5_hits": row[5],
        "top9_hits": row[6],
        "top10_hits": row[7],
        "top15_hits": row[8],
        "strong_pack_hits": json.loads(row[9] or "{}"),
    }


def settled_history(conn: sqlite3.Connection, limit: int = 90) -> list[dict]:
    rows = conn.execute(
        """
        SELECT based_on_date,target_date,candidates_json,strong_packs_json,created_at,actual_date,
               actual_numbers_json,top5_hits,top9_hits,top10_hits,top15_hits,strong_pack_hits_json
        FROM predictions
        WHERE status='settled'
        ORDER BY actual_date DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    history = []
    for row in rows:
        history.append(
            {
                "based_on_date": row[0],
                "target_date": row[1],
                "candidates": json.loads(row[2] or "[]"),
                "strong_packs": json.loads(row[3] or "{}"),
                "created_at": row[4],
                "actual_date": row[5],
                "actual_numbers": json.loads(row[6] or "[]"),
                "top5_hits": row[7],
                "top9_hits": row[8],
                "top10_hits": row[9],
                "top15_hits": row[10],
                "strong_pack_hits": json.loads(row[11] or "{}"),
            }
        )
    return history


def settle_predictions(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id,based_on_date,candidates_json,strong_packs_json FROM predictions WHERE status='pending'"
    ).fetchall()
    settled = 0
    for row in rows:
        actual = conn.execute(
            "SELECT draw_date,n1,n2,n3,n4,n5 FROM draws WHERE draw_date > ? ORDER BY draw_date LIMIT 1",
            (row[1],),
        ).fetchone()
        if not actual:
            continue
        actual_numbers = set(int(number) for number in actual[1:6])
        candidates = json.loads(row[2])
        ranked = [int(item["number"]) for item in candidates]
        packs = json.loads(row[3])
        pack_hits = {}
        for key, pack in packs.items():
            numbers = [int(number) for number in pack.get("numbers", [])]
            hits = sorted(set(numbers) & actual_numbers)
            pack_hits[key] = {
                "numbers": numbers,
                "hits": len(hits),
                "hit_numbers": hits,
                "passed": len(hits) >= int(pack.get("hit_goal", 1)),
            }
        conn.execute(
            """
            UPDATE predictions
            SET settled_at=?, actual_date=?, actual_numbers_json=?,
                top5_hits=?, top9_hits=?, top10_hits=?, top15_hits=?,
                strong_pack_hits_json=?, status='settled'
            WHERE id=?
            """,
            (
                stamp(),
                actual[0],
                json.dumps(sorted(actual_numbers), ensure_ascii=False),
                len(set(ranked[:5]) & actual_numbers),
                len(set(ranked[:9]) & actual_numbers),
                len(set(ranked[:10]) & actual_numbers),
                len(set(ranked[:15]) & actual_numbers),
                json.dumps(pack_hits, ensure_ascii=False),
                row[0],
            ),
        )
        settled += 1
    conn.commit()
    return settled


def store_prediction(conn: sqlite3.Connection, analysis: dict) -> str:
    based_on = analysis["latest_draw"]["draw_date"]
    target = analysis["target_draw_date"]
    candidates_json = json.dumps(analysis["candidates"], ensure_ascii=False)
    packs_json = json.dumps(analysis["strong_packs"], ensure_ascii=False)
    existing = conn.execute("SELECT id FROM predictions WHERE based_on_date=?", (based_on,)).fetchone()
    if existing:
        conn.execute(
            """
            INSERT INTO prediction_snapshots(
                based_on_date,target_date,candidates_json,strong_packs_json,created_at,snapshot_reason
            )
            VALUES(?,?,?,?,?,?)
            """,
            (based_on, target, candidates_json, packs_json, stamp(), "rerun_snapshot_official_preserved"),
        )
        conn.commit()
        return "snapshot_preserved_existing_prediction"
    conn.execute(
        """
        INSERT INTO predictions(based_on_date,target_date,candidates_json,strong_packs_json,created_at,status)
        VALUES(?,?,?,?,?,'pending')
        """,
        (based_on, target, candidates_json, packs_json, stamp()),
    )
    conn.execute(
        """
        INSERT INTO prediction_snapshots(
            based_on_date,target_date,candidates_json,strong_packs_json,created_at,snapshot_reason
        )
        VALUES(?,?,?,?,?,?)
        """,
        (based_on, target, candidates_json, packs_json, stamp(), "official_prediction_created"),
    )
    conn.commit()
    return "inserted"


def analyze(draws: list[dict], rounds: int, settled_history_rows: list[dict] | None = None) -> dict:
    if not draws:
        raise RuntimeError("No draw data is available.")
    latest = draws[-1]
    target_date = next_draw_date(latest["draw_date"])
    base_weights, model_backtest = model_backtest_weights(draws, rounds=min(rounds, 120))
    low_hit_review = low_hit_regime_review(settled_history_rows)
    failure_memory = failure_memory_from_settled(settled_history_rows)
    weights, rolling_adjustment = rolling_error_adjusted_weights(draws, base_weights, rounds=rounds)
    weights, low_hit_review = apply_low_hit_regime_shift(weights, low_hit_review)
    low_hit_review["failure_memory"] = failure_memory
    scored = score_numbers(draws, weights, failure_memory)
    candidates, front9_escape_correction = apply_front9_escape_correction(
        scored["candidates"],
        failure_memory,
        settled_history_rows,
    )
    scored["candidates"] = candidates
    packs = build_packs(candidates)
    backtest_result = backtest(draws, rounds=rounds, weights=weights)
    metadata = history_metadata()
    completeness = history_completeness(len(draws), metadata)
    fresh = freshness(latest["draw_date"], target_date)
    integrity = data_integrity_gate(draws, metadata)
    gate = release_gate(backtest_result, completeness, fresh)
    if integrity["status"] != "passed":
        gate = {
            "status": "data_blocked",
            "reason": "資料完整性稽核未通過，禁止包裝成高信心。",
            "rule": integrity["rule"],
        }
    high_confidence = [
        item
        for item in candidates[:9]
        if item["confidence_index"] >= 86 and item["support_models"] >= 3
    ]
    analysis = {
        "engine_version": "ghana_daywa39_precision_spec_v4_front9_escape_20260728",
        "generated_at_taiwan": stamp(now_taiwan()),
        "generated_at_draw_timezone": stamp(now_draw_timezone()),
        "game_spec": asdict(SPEC),
        "latest_draw": latest,
        "target_draw_date": target_date,
        "draw_count": len(draws),
        "history_metadata": metadata,
        "history_completeness": completeness,
        "freshness": fresh,
        "release_gate": gate,
        "model_weights": weights,
        "base_model_weights": base_weights,
        "model_backtest": model_backtest,
        "rolling_error_adjustment": rolling_adjustment,
        "low_hit_regime_shift": low_hit_review,
        "front9_escape_correction": front9_escape_correction,
        "data_integrity_gate": integrity,
        "backtest": backtest_result,
        "candidates": candidates,
        "strong_packs": packs,
        "high_confidence_watch": high_confidence,
        "date_cycle_numbers": date_cycle_numbers(target_date),
        "ironlaw_spec": {
            "data_first": "SQLite與CSV先更新，才產生候選號。",
            "multi_window": "同時看5、10、20、50、100、300、720期。",
            "strong_pack_layers": "單支、2中1、3中1、5中2、9中3。",
            "settlement": "下一期開出後結算Top5/Top9/Top10/Top15與強牌。",
            "cross_validation": "多模型權重由滾動回測仲裁。",
            "rolling_error_rebuild": "每期開獎結算後，以12/30/90期錯誤檢討重新調整全部模型權重。",
            "low_hit_regime_shift": "近期實戰命中低於隨機基準或零命中偏高時，啟動漏抓回補、落空降權與模型權重轉換。",
            "front9_escape_correction": "每期檢測命中是否掉到第10到15名；若有外溢，立即將第二層強訊號壓回前九。",
            "strong_single_guard": "最強獨隻1中1不得直接使用最新開獎號，必須通過獨立守門。",
            "transparent_report": "輸出JSON、Markdown與HTML戰報。",
            "no_single_model": "至少8個模型來源合成，不讓單一條件主導。",
            "freshness_label": "標示迦納官方最新資料日期與台灣開獎更新時間。",
        },
        "risk_notice": "樂透為隨機遊戲，本系統只做資料研究與風險標示，不保證命中或獲利。",
    }
    standard_report.decorate_analysis(analysis)
    return analysis


def mark_hits(numbers: list[int], actual: set[int] | None = None) -> str:
    if not actual:
        return fmt_numbers(numbers)
    parts = []
    for number in numbers:
        text = f"{int(number):02d}"
        parts.append(f"**{text}**" if int(number) in actual else text)
    return " ".join(parts)


def pct(value, digits: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def num_text(value, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "-"


def top_numbers(analysis: dict, count: int) -> list[int]:
    return [int(item["number"]) for item in analysis.get("candidates", [])[:count]]


def strong_single_numbers(analysis: dict) -> list[int]:
    pack = ((analysis.get("strong_packs") or {}).get("strong_single") or {})
    numbers = pack.get("numbers") or []
    return [int(number) for number in numbers[:1]] if numbers else top_numbers(analysis, 1)


def precision_status(analysis: dict) -> str:
    gate = analysis.get("release_gate") or {}
    completeness = (analysis.get("history_completeness") or {}).get("status", "-")
    freshness_status = (analysis.get("freshness") or {}).get("status", "-")
    if gate.get("status") == "official_watch":
        return "通過研究守門，可列正式觀察"
    if completeness != "complete":
        return "官方歷史不足3000期，精準研究觀察"
    if freshness_status != "fresh":
        return "資料新鮮度待確認，暫不主推"
    return "研究觀察"


def ghana39_time_rows(analysis: dict) -> list[list[str]]:
    fresh = analysis.get("freshness") or {}
    return [
        ["官方資料先行", "先更新 NLA winning-numbers 官方歷史 CSV 與 SQLite，才允許產生候選號。"],
        ["開獎時間", f"依使用者指定，每日台灣時間 {fresh.get('daily_draw_time_taiwan', '17:30')} 作為更新基準。"],
        ["開獎後結算", "下一筆官方資料進來後，自動結算 Top5 / Top9 / Top10 / Top15 與強牌。"],
        ["完整重算", "每次執行皆重新評分、重新回測、重建 Markdown / HTML / JSON 戰報。"],
        ["發布守門", f"{(analysis.get('release_gate') or {}).get('status', '-')}：{(analysis.get('release_gate') or {}).get('reason', '-')}"],
    ]


def precision_candidate_rows(analysis: dict, limit: int = 9) -> list[list]:
    rows = []
    for item in analysis.get("candidates", [])[:limit]:
        conclusion = "前九核心" if item.get("rank", 99) <= 9 else "備選觀察"
        if item.get("confidence_index", 0) >= 86 and item.get("support_models", 0) >= 3:
            conclusion = "高信心觀察"
        rows.append(
            [
                f"{int(item['number']):02d}",
                item.get("rank", "-"),
                item.get("score", "-"),
                item.get("confidence_index", "-"),
                item.get("model_probability_index", "-"),
                item.get("omission", "-"),
                item.get("support_models", "-"),
                "、".join((item.get("reasons") or [])[:5]),
                conclusion,
            ]
        )
    return rows


def precision_verification_rows(analysis: dict, limit: int = 9) -> list[list]:
    rows = []
    for item in analysis.get("candidates", [])[:limit]:
        reasons = item.get("reasons") or []
        omission = item.get("omission", "-")
        support = item.get("support_models", "-")
        rank = item.get("rank", "-")
        rows.append(
            [
                f"{int(item['number']):02d}",
                rank,
                " / ".join(reasons[:3]) if reasons else "綜合模型",
                f"{support} 個模型支援；模型指標 {item.get('model_probability_index', '-')}",
                f"目前遺漏 {omission} 期；信心 {item.get('confidence_index', '-')}",
                "通過前九核心守門" if int(rank) <= 9 else "列入備選",
                "可進強牌配置" if int(rank) <= 9 else "不進強牌",
            ]
        )
    return rows


def precision_pack_rows(analysis: dict) -> list[list]:
    backtest = analysis.get("backtest") or {}
    pack_summary = backtest.get("pack_summary") or {}
    rows = []
    for key, pack in (analysis.get("strong_packs") or {}).items():
        prob = pack.get("theoretical_probability") or {}
        summary = pack_summary.get(key) or {}
        pass_rate = summary.get("pass_rate")
        status = "研究觀察"
        if pass_rate is not None:
            if float(pass_rate) >= 0.85:
                status = "高穩定"
            elif float(pass_rate) >= 0.65:
                status = "可觀察"
            else:
                status = "未達強推"
        rows.append(
            [
                pack.get("name", key),
                fmt_numbers(pack.get("numbers", [])),
                f"{pack.get('hit_goal', '-')} 中",
                f"{prob.get('probability', '-')} / 約1中{prob.get('odds_1_in', '-')}",
                backtest.get("rounds", 0),
                pct(pass_rate),
                summary.get("avg_hits", "-"),
                status,
            ]
        )
    return rows


def precision_backtest_rows(analysis: dict) -> list[list]:
    backtest = analysis.get("backtest") or {}
    random = backtest.get("random_expectation") or {}
    rows = []
    for key, label in [("top5", "Top5"), ("top9", "Top9"), ("top10", "Top10"), ("top15", "Top15")]:
        avg = backtest.get(f"{key}_avg_hits")
        rand = random.get(key)
        edge = None
        if avg is not None and rand is not None:
            edge = round(float(avg) - float(rand), 4)
        rows.append([label, backtest.get("rounds", 0), avg if avg is not None else "-", rand if rand is not None else "-", edge if edge is not None else "-"])
    return rows


def precision_model_rows(analysis: dict) -> list[list]:
    model_backtest = analysis.get("model_backtest") or {}
    weights = analysis.get("model_weights") or {}
    rolling = (analysis.get("rolling_error_adjustment") or {}).get("models") or {}
    rows = []
    for key, metrics in (model_backtest.get("models") or {}).items():
        edge = metrics.get("edge_vs_random")
        rolling_item = rolling.get(key) or {}
        action = rolling_item.get("action") or ("加權採用" if float(weights.get(key, 0) or 0) >= 0.08 else "低權重保留")
        weight_text = weights.get(key, "-")
        if rolling_item:
            weight_text = f"{weight_text} / x{rolling_item.get('correction', '-')}"
        rows.append(
            [
                metrics.get("label", MODEL_LABELS.get(key, key)),
                model_backtest.get("rounds", 0),
                metrics.get("top9_avg_hits", "-"),
                edge if edge is not None else "-",
                weight_text,
                action,
            ]
        )
    return rows


def precision_guard_rows(analysis: dict) -> list[list]:
    fresh = analysis.get("freshness") or {}
    completeness = analysis.get("history_completeness") or {}
    gate = analysis.get("release_gate") or {}
    low_hit = analysis.get("low_hit_regime_shift") or {}
    memory = low_hit.get("failure_memory") or {}
    return [
        ["資料庫", f"{analysis.get('draw_count', 0)} 筆", f"{completeness.get('status', '-')}；{completeness.get('note', '')}"],
        ["新鮮度", fresh.get("status", "-"), f"最新 {fresh.get('latest_draw_date', '-')}，落後 {fresh.get('age_days', '-')} 天"],
        ["資料真實性", (analysis.get("data_integrity_gate") or {}).get("status", "-"), (analysis.get("data_integrity_gate") or {}).get("rule", "-")],
        ["錯誤模組重算", (analysis.get("rolling_error_adjustment") or {}).get("status", "-"), (analysis.get("rolling_error_adjustment") or {}).get("rule", "-")],
        ["低命中權重轉換", low_hit.get("status", "-"), f"{low_hit.get('mode', '-')}；嚴重度 {low_hit.get('severity', '-')}；樣本 {low_hit.get('basis_window', '-')} 期"],
        ["漏抓回補記憶", memory.get("status", "-"), memory.get("rule", "-")],
        ["官方來源", SPEC.official_reference, "NLA winning-numbers 官方接口"],
        ["發布關卡", gate.get("status", "-"), gate.get("reason", "-")],
        ["風險標示", "必要", analysis.get("risk_notice", "-")],
    ]


def precision_settlement_rows(settled: dict) -> list[list]:
    if not settled:
        return [["尚無已結算上期", "-", "-", "-", "新資料進來後會自動結算"]]
    actual = set(int(number) for number in settled.get("actual_numbers", []))
    rows = [
        [
            "候選命中",
            fmt_numbers(settled.get("actual_numbers", [])),
            f"Top5 {settled.get('top5_hits', 0)}",
            f"Top9 {settled.get('top9_hits', 0)} / Top10 {settled.get('top10_hits', 0)} / Top15 {settled.get('top15_hits', 0)}",
            f"{settled.get('based_on_date')} -> {settled.get('actual_date')}",
        ]
    ]
    for key, pack in (settled.get("strong_packs") or {}).items():
        hit_row = (settled.get("strong_pack_hits") or {}).get(key, {})
        rows.append(
            [
                pack.get("name", key),
                mark_hits(pack.get("numbers", []), actual).replace("**", ""),
                hit_row.get("hits", 0),
                "達標" if hit_row.get("passed") else "未達標",
                f"命中：{fmt_numbers(hit_row.get('hit_numbers', [])) or '-'}",
            ]
        )
    return rows


def build_markdown(analysis: dict, settled: dict) -> str:
    latest = analysis["latest_draw"]
    top9 = top_numbers(analysis, 9)
    top15 = top_numbers(analysis, 15)
    bt = analysis["backtest"]
    lines = [
        "# 迦納彩39 精準預測戰報 - 非洲迦納彩 Daywa 5/39 Direct",
        "",
        f"- 系統版本：{analysis['engine_version']}",
        f"- 產生時間（台灣）：{analysis['generated_at_taiwan']}",
        f"- 最新開獎（台灣日期）：{latest['draw_date']} / {fmt_numbers(latest['numbers'])}",
        f"- 預測目標期（台灣日期）：{analysis['target_draw_date']}",
        f"- 每日開獎時間（台灣）：{analysis['freshness']['daily_draw_time_taiwan']}",
        f"- 本期開獎時間（台灣）：{analysis['freshness']['target_taiwan_safe_update_time']}",
        f"- 資料筆數：{analysis['draw_count']} / 完整度：{analysis['history_completeness']['status']}",
        f"- 資料新鮮度：{analysis['freshness']['status']} / 落後 {analysis['freshness']['age_days']} 天",
        f"- 發布關卡：{analysis['release_gate']['status']} / {analysis['release_gate']['reason']}",
        f"- 風險聲明：{analysis['risk_notice']}",
        "",
        "## 核心決策",
        "",
        f"- 發布等級：{precision_status(analysis)}",
        f"- 獨隻：{fmt_numbers(strong_single_numbers(analysis))}",
        f"- 前九核心：{fmt_numbers(top9)}",
        f"- 前十五觀察：{fmt_numbers(top15)}",
        f"- Top9 回測：平均 {bt.get('top9_avg_hits', '-')} / 隨機 {(bt.get('random_expectation') or {}).get('top9', '-')} / 差值 {bt.get('top9_edge_vs_random', '-')}",
        "",
        "## 每日更新鐵律時間表",
    ]
    for row in ghana39_time_rows(analysis):
        lines.append(f"- {row[0]}：{row[1]}")
    lines.extend(
        [
            "",
            "## 下期精算前9名",
            "",
            "| 號碼 | 排名 | 分數 | 信心 | 機率指標 | 遺漏 | 驗算數 | 來源 | 結論 |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in precision_candidate_rows(analysis, 9):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(
        [
            "",
            "## 強牌組精算",
            "",
            "| 類型 | 號碼 | 目標 | 理論機率 | 回測期 | 達標率 | 平均命中 | 判定 |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in precision_pack_rows(analysis):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(["", "## 逐號驗算", "", "| 號碼 | 排名 | 版路分類 | 交叉驗算 | 穩定與遺漏 | 守門 | 結論 |", "| ---: | ---: | --- | --- | --- | --- | --- |"])
    for row in precision_verification_rows(analysis, 9):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(["", "## 模型回測摘要", "", "| 模型 | 回測期 | Top9平均 | 對隨機差值 | 本期權重 | 動作 |", "| --- | ---: | ---: | ---: | ---: | --- |"])
    for row in precision_model_rows(analysis):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(["", "## 整體回測", "", "| 項目 | 回測期 | 平均命中 | 隨機期望 | 差值 |", "| --- | ---: | ---: | ---: | ---: |"])
    for row in precision_backtest_rows(analysis):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(["", "## 上期命中檢討", "", "| 項目 | 號碼 | 命中 | 結果 | 說明 |", "| --- | --- | ---: | --- | --- |"])
    for row in precision_settlement_rows(settled):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(["", "## 鐵律守門", "", "| 守門 | 狀態 | 說明 |", "| --- | --- | --- |"])
    for row in precision_guard_rows(analysis):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    if settled:
        lines.extend(["", "## 強牌檢討", "- 已依實際開獎逐項結算，未達標者下一期自動降權觀察。"])
    return "\n".join(lines) + "\n"


def table_html(headers: list[str], rows: list[list], empty_text: str = "目前沒有資料") -> str:
    head = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>")
    if not body:
        body.append(f'<tr><td colspan="{len(headers)}">{html.escape(empty_text)}</td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def build_html(analysis: dict, settled: dict) -> str:
    latest = analysis["latest_draw"]
    top9 = top_numbers(analysis, 9)
    top15 = top_numbers(analysis, 15)
    fresh = analysis["freshness"]
    backtest = analysis["backtest"]
    precision_label = precision_status(analysis)
    high_watch = fmt_numbers([item["number"] for item in analysis.get("high_confidence_watch", [])]) or "本期未過高信心守門"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>迦納彩39 精準預測戰報 - 非洲迦納彩 Daywa 5/39 Direct</title>
  <style>
    body {{ margin:0; font-family:"Microsoft JhengHei", Arial, sans-serif; background:#f5f7fb; color:#172033; }}
    header {{ background:#111827; color:white; padding:22px 24px; }}
    header h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    header p {{ margin:4px 0; color:#dbeafe; }}
    main {{ max-width:1180px; margin:0 auto; padding:18px; }}
    .tabs {{ display:flex; gap:8px; flex-wrap:wrap; position:sticky; top:0; z-index:5; background:#f5f7fb; padding:10px 0; }}
    .tabs button {{ border:1px solid #cbd5e1; background:white; border-radius:7px; padding:10px 14px; font-weight:800; cursor:pointer; color:#172033; }}
    .tabs button.active {{ background:#0f766e; color:white; border-color:#0f766e; }}
    .panel {{ display:none; }}
    .panel.active {{ display:block; }}
    .band {{ background:white; border:1px solid #dde3ee; border-radius:8px; padding:16px; margin:14px 0; overflow:auto; }}
    .band.warn {{ background:#fff7ed; border-color:#fed7aa; }}
    .band.date {{ background:#ecfeff; border-color:#67e8f9; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:10px; }}
    .metric {{ border:1px solid #dde3ee; border-radius:8px; padding:12px; background:#fbfcff; }}
    .label {{ color:#64748b; font-size:13px; }}
    .value {{ font-size:20px; font-weight:800; margin-top:6px; }}
    .big {{ font-size:24px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th,td {{ border-bottom:1px solid #e5eaf2; padding:8px; text-align:left; vertical-align:top; }}
    th {{ background:#f1f5f9; color:#334155; }}
    .numbers {{ font-weight:900; letter-spacing:0; }}
    .small {{ font-size:13px; line-height:1.55; color:#475569; }}
    @media(max-width:680px) {{ main {{ padding:10px; }} header {{ padding:16px; }} table {{ min-width:760px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>迦納彩39 精準預測戰報</h1>
    <p>非洲迦納彩 Daywa 5/39 Direct / 產生時間 {html.escape(analysis['generated_at_taiwan'])}</p>
    <p>資料依據：{html.escape(latest['draw_date'])} / {html.escape(fmt_numbers(latest['numbers']))}　預測目標：{html.escape(analysis['target_draw_date'])} 台灣時間 {html.escape(fresh.get('daily_draw_time_taiwan', '17:30'))}</p>
  </header>
  <main>
    <nav class="tabs">
      <button class="active" data-tab="prediction">下期預測</button>
      <button data-tab="review">命中檢討</button>
      <button data-tab="models">模型回測</button>
      <button data-tab="system">鐵律守門</button>
    </nav>
    <section class="band date">
      <h2>本報表日期對照</h2>
      <div class="grid">
        <div class="metric"><div class="label">官方最新開獎</div><div class="value">{html.escape(latest['draw_date'])}</div></div>
        <div class="metric"><div class="label">最新號碼</div><div class="value numbers">{html.escape(fmt_numbers(latest['numbers']))}</div></div>
        <div class="metric"><div class="label">下期預測日</div><div class="value">{html.escape(analysis['target_draw_date'])}</div></div>
        <div class="metric"><div class="label">台灣開獎時間</div><div class="value">{html.escape(fresh.get('target_taiwan_safe_update_time', '-'))}</div></div>
        <div class="metric"><div class="label">官方歷史筆數</div><div class="value">{analysis['draw_count']}</div></div>
        <div class="metric"><div class="label">戰報狀態</div><div class="value">{html.escape(precision_label)}</div></div>
      </div>
    </section>
    <section id="prediction" class="panel active">
      <div class="band">
        <h2>核心決策</h2>
        <div class="grid">
          <div class="metric"><div class="label">獨隻</div><div class="value big numbers">{html.escape(fmt_numbers(strong_single_numbers(analysis)))}</div></div>
          <div class="metric"><div class="label">九碼核心</div><div class="value numbers">{html.escape(fmt_numbers(top9))}</div></div>
          <div class="metric"><div class="label">十五碼觀察</div><div class="value numbers">{html.escape(fmt_numbers(top15))}</div></div>
          <div class="metric"><div class="label">高信心守門</div><div class="value">{html.escape(high_watch)}</div></div>
        </div>
        <p class="small">運算原則：官方歷史先更新，候選號再由多模型交叉驗算、滾動回測、強牌守門與資料新鮮度共同放行。</p>
      </div>
      <div class="band">
        <h2>每日更新鐵律時間表</h2>
        {table_html(["項目", "內容"], ghana39_time_rows(analysis))}
      </div>
      <div class="band">
        <h2>下期精算前9名</h2>
        {table_html(["號碼", "排名", "分數", "信心", "機率指標", "遺漏", "驗算數", "來源", "結論"], precision_candidate_rows(analysis, 9))}
      </div>
      <div class="band">
        <h2>逐號驗算</h2>
        {table_html(["號碼", "排名", "版路分類", "交叉驗算", "穩定與遺漏", "守門", "結論"], precision_verification_rows(analysis, 9))}
      </div>
      <div class="band">
        <h2>強牌組精算</h2>
        {table_html(["類型", "號碼", "目標", "理論機率", "回測期", "達標率", "平均命中", "判定"], precision_pack_rows(analysis))}
      </div>
    </section>
    <section id="review" class="panel">
      <div class="band">
        <h2>上期命中檢討</h2>
        {table_html(["項目", "號碼", "命中", "結果", "說明"], precision_settlement_rows(settled))}
      </div>
      <div class="band warn">
        <h2>資料新鮮度提醒</h2>
        <p>最新官方資料日期：{html.escape(fresh.get('latest_draw_date', '-'))}；台灣今日：{html.escape(fresh.get('taiwan_today', '-'))}；落後 {html.escape(str(fresh.get('age_days', '-')))} 天。</p>
        <p>{html.escape(analysis['release_gate']['reason'])}</p>
      </div>
    </section>
    <section id="models" class="panel">
      <div class="band">
        <h2>整體回測</h2>
        {table_html(["項目", "回測期", "平均命中", "隨機期望", "差值"], precision_backtest_rows(analysis))}
      </div>
      <div class="band">
        <h2>多模型競賽回測</h2>
        {table_html(["模型", "回測期", "Top9平均", "對隨機差值", "本期權重", "動作"], precision_model_rows(analysis))}
      </div>
      <div class="band">
        <h2>強牌實戰統計</h2>
        {table_html(["類型", "號碼", "目標", "理論機率", "回測期", "達標率", "平均命中", "判定"], precision_pack_rows(analysis))}
      </div>
    </section>
    <section id="system" class="panel">
      <div class="band">
        <h2>鐵律守門</h2>
        {table_html(["守門", "狀態", "說明"], precision_guard_rows(analysis))}
      </div>
      <div class="band warn">
        <h2>風險聲明</h2>
        <p>{html.escape(analysis['risk_notice'])}</p>
      </div>
    </section>
  </main>
  <script>
    document.querySelectorAll('.tabs button').forEach(btn => btn.addEventListener('click', () => {{
      document.querySelectorAll('.tabs button').forEach(item => item.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(panel => panel.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    }}));
  </script>
</body>
</html>
"""


def build_mobile_html(analysis: dict, settled: dict) -> str:
    latest = analysis["latest_draw"]
    fresh = analysis["freshness"]
    top9 = top_numbers(analysis, 9)
    top15 = top_numbers(analysis, 15)
    bt = analysis.get("backtest") or {}
    precision_label = precision_status(analysis)
    updated = analysis.get("generated_at_taiwan", "-")
    high_watch = fmt_numbers([item["number"] for item in analysis.get("high_confidence_watch", [])]) or "未過高信心守門"
    core_cards = [
        ["獨隻", fmt_numbers(strong_single_numbers(analysis)), "獨立守門；禁止直接用最新開獎號"],
        ["前九核心", fmt_numbers(top9), "核心候選"],
        ["前十五觀察", fmt_numbers(top15), "備選與版路觀察"],
        ["發布等級", precision_label, (analysis.get("release_gate") or {}).get("reason", "-")],
    ]
    card_html = "".join(
        f'<section class="card"><div class="label">{html.escape(label)}</div><div class="value">{html.escape(value)}</div><p>{html.escape(note)}</p></section>'
        for label, value, note in core_cards
    )
    candidate_cards = []
    for row in precision_candidate_rows(analysis, 9):
        number, rank, score, confidence, probability, omission, support, source, conclusion = row
        candidate_cards.append(
            f"""
            <article class="number-card">
              <div class="ball">{html.escape(str(number))}</div>
              <div>
                <h3>排名 {html.escape(str(rank))} / {html.escape(str(conclusion))}</h3>
                <p>信心 {html.escape(str(confidence))}；分數 {html.escape(str(score))}；機率指標 {html.escape(str(probability))}</p>
                <p>遺漏 {html.escape(str(omission))} 期；{html.escape(str(support))} 個模型驗算</p>
                <p class="source">{html.escape(str(source))}</p>
              </div>
            </article>
            """
        )
    pack_cards = []
    for row in precision_pack_rows(analysis):
        label, numbers, goal, probability, rounds, pass_rate, avg_hits, status = row
        pack_cards.append(
            f"""
            <article class="pack-card">
              <h3>{html.escape(str(label))}</h3>
              <div class="packnums">{html.escape(str(numbers))}</div>
              <p>目標 {html.escape(str(goal))}；回測 {html.escape(str(rounds))} 期</p>
              <p>達標率 {html.escape(str(pass_rate))}；平均命中 {html.escape(str(avg_hits))}；{html.escape(str(status))}</p>
              <p class="source">理論機率：{html.escape(str(probability))}</p>
            </article>
            """
        )
    model_rows = table_html(["模型", "回測期", "Top9平均", "差值", "權重", "動作"], precision_model_rows(analysis))
    review_rows = table_html(["項目", "號碼", "命中", "結果", "說明"], precision_settlement_rows(settled))
    guard_rows = table_html(["守門", "狀態", "說明"], precision_guard_rows(analysis))
    backtest_rows = table_html(["項目", "回測期", "平均", "隨機", "差值"], precision_backtest_rows(analysis))
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <title>迦納彩39 雲端手機版 - 非洲迦納彩 Daywa 5/39</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#64748b; --line:#dbe4ef; --brand:#0f766e; --hot:#b91c1c; --soft:#f6f8fb; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--soft); color:var(--ink); font-family:"Microsoft JhengHei", Arial, sans-serif; }}
    header {{ padding:18px 16px 14px; background:#111827; color:white; position:sticky; top:0; z-index:10; box-shadow:0 8px 20px rgba(15,23,42,.16); }}
    h1 {{ margin:0 0 6px; font-size:22px; letter-spacing:0; }}
    header p {{ margin:4px 0; color:#dbeafe; line-height:1.45; font-size:13px; }}
    main {{ padding:12px; max-width:720px; margin:0 auto 70px; }}
    .status {{ background:#ecfeff; border:1px solid #67e8f9; border-radius:8px; padding:12px; margin-bottom:12px; }}
    .status p {{ margin:4px 0; line-height:1.5; }}
    .grid {{ display:grid; gap:10px; }}
    .card, .section, .number-card, .pack-card {{ background:white; border:1px solid var(--line); border-radius:8px; padding:12px; }}
    .label {{ color:var(--muted); font-size:13px; font-weight:800; }}
    .value {{ margin-top:5px; font-size:21px; font-weight:900; line-height:1.25; color:#0f172a; overflow-wrap:anywhere; }}
    .card p, .section p, .pack-card p, .number-card p {{ margin:6px 0 0; line-height:1.45; color:#475569; font-size:13px; }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    h3 {{ margin:0; font-size:15px; }}
    .section {{ margin-top:12px; overflow:auto; }}
    .number-list, .pack-list {{ display:grid; gap:8px; }}
    .number-card {{ display:grid; grid-template-columns:48px 1fr; gap:10px; align-items:start; }}
    .ball {{ width:44px; height:44px; border-radius:999px; border:2px solid var(--hot); color:var(--hot); display:flex; align-items:center; justify-content:center; font-size:20px; font-weight:900; }}
    .packnums {{ font-size:20px; font-weight:900; color:#0f172a; margin-top:6px; overflow-wrap:anywhere; }}
    .source {{ color:#0f766e !important; font-weight:700; }}
    .tabs {{ position:fixed; left:0; right:0; bottom:0; z-index:12; display:grid; grid-template-columns:repeat(4,1fr); background:white; border-top:1px solid var(--line); padding:7px 6px calc(7px + env(safe-area-inset-bottom)); gap:6px; }}
    .tabs a {{ text-decoration:none; color:#334155; text-align:center; font-size:12px; font-weight:900; padding:8px 4px; border-radius:7px; background:#f8fafc; }}
    table {{ width:100%; border-collapse:collapse; min-width:680px; font-size:13px; }}
    th,td {{ border-bottom:1px solid #e5eaf2; padding:8px; text-align:left; vertical-align:top; }}
    th {{ background:#f1f5f9; color:#334155; }}
    .risk {{ background:#fff7ed; border-color:#fed7aa; }}
  </style>
</head>
<body>
  <header>
    <h1>迦納彩39 雲端手機版</h1>
    <p>非洲迦納彩 Daywa 5/39 Direct / {html.escape(updated)}</p>
    <p>最新 {html.escape(latest['draw_date'])}：{html.escape(fmt_numbers(latest['numbers']))}；預測 {html.escape(analysis['target_draw_date'])} 台灣 {html.escape(fresh.get('daily_draw_time_taiwan', '17:30'))}</p>
  </header>
  <main>
    <section class="status">
      <p><strong>官方資料：</strong>{analysis['draw_count']} 筆，{html.escape((analysis.get('history_completeness') or {}).get('status', '-'))}</p>
      <p><strong>新鮮度：</strong>{html.escape(fresh.get('status', '-'))}，最新 {html.escape(fresh.get('latest_draw_date', '-'))}，落後 {html.escape(str(fresh.get('age_days', '-')))} 天</p>
      <p><strong>高信心：</strong>{html.escape(high_watch)}</p>
    </section>
    <section id="core" class="grid">
      {card_html}
    </section>
    <section id="numbers" class="section">
      <h2>下期精算前9名</h2>
      <div class="number-list">{''.join(candidate_cards)}</div>
    </section>
    <section id="packs" class="section">
      <h2>強牌組精算</h2>
      <div class="pack-list">{''.join(pack_cards)}</div>
    </section>
    <section id="review" class="section">
      <h2>上期命中檢討</h2>
      {review_rows}
    </section>
    <section id="models" class="section">
      <h2>整體回測</h2>
      {backtest_rows}
    </section>
    <section class="section">
      <h2>多模型競賽回測</h2>
      {model_rows}
    </section>
    <section id="guard" class="section risk">
      <h2>鐵律守門</h2>
      {guard_rows}
      <p>{html.escape(analysis['risk_notice'])}</p>
    </section>
  </main>
  <nav class="tabs">
    <a href="#core">核心</a>
    <a href="#numbers">前9</a>
    <a href="#packs">強牌</a>
    <a href="#guard">守門</a>
  </nav>
</body>
</html>"""


def build_design_doc(analysis: dict) -> str:
    return f"""# 非洲迦納彩 Daywa 5/39 Direct 預測系統設計書

## 玩法規格

- 名稱：{SPEC.display_name}
- 格式：{SPEC.draw_size}/{SPEC.number_max}，從 {SPEC.number_min:02d} 到 {SPEC.number_max:02d} 選 {SPEC.draw_size} 顆。
- 資料時區：{SPEC.draw_timezone}
- 報表時區：{SPEC.report_timezone}
- 每日開獎時間：台灣時間 {SPEC.draw_time_taiwan}

## 標準規格模組對應

1. 資料先行：`draws` 表與 CSV 先完成匯入，才允許產生候選號。
2. 多窗口分析：5、10、20、50、100、300、720 期共同參與評分。
3. 強牌分層：最強單支、2中1、3中1、5中2、9中3。
4. 上一期結算：下一期資料進來後，自動結算 Top5 / Top9 / Top10 / Top15 與強牌。
5. 交叉驗證：8 個模型用滾動回測產生權重，不讓單一模型主導。
6. 戰報透明：輸出 `latest_analysis.json`、`latest_battle_report.md`、`latest_battle_report.html`。
7. 不迷信單一模型：頻率、遺漏、拖牌、牌型、尾數區間、和值、趨勢、日期循環共同仲裁。
8. 資料新鮮度：標示最新開獎台灣日期、落後天數、台灣開獎時間。

## 模型層

- 多窗口頻率：短中長期熱度平衡。
- 遺漏相位：避免只追冷號，改用期望遺漏區間。
- 拖牌關聯：以上期號碼當錨點，檢查歷史下一期共現。
- 牌型跟隨：奇偶、大小、和值、跨度、尾數、區間相似後續。
- 尾數區間平衡：近期尾數與區間缺口。
- 和值鄰近：與上期和值/跨度接近的歷史後續。
- 趨勢轉折：短中長頻率差與遺漏壓力。
- 日期循環：目標日期衍生號，只作低權重輔助。

## 目前狀態

- 資料筆數：{analysis['draw_count']}
- 最新開獎：{analysis['latest_draw']['draw_date']} / {fmt_numbers(analysis['latest_draw']['numbers'])}
- 預測目標：{analysis['target_draw_date']}
- 發布關卡：{analysis['release_gate']['status']}
- 風險聲明：{analysis['risk_notice']}
"""


def save_outputs(analysis: dict, settled: dict, history: list[dict] | None = None) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    history = history or []
    outputs = standard_report.build_outputs(analysis, settled, history)
    LATEST_JSON.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    BATTLE_MD.write_text(outputs["markdown"], encoding="utf-8")
    BATTLE_HTML.write_text(outputs["desktop_html"], encoding="utf-8")
    PRECISION_HTML.write_text(outputs["desktop_html"], encoding="utf-8")
    MOBILE_JSON.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    for filename, content in outputs["mobile_pages"].items():
        (SITE_DIR / filename).write_text(content, encoding="utf-8")
    for filename, content in outputs["report_pages"].items():
        (REPORT_DIR / filename).write_text(content, encoding="utf-8")
    (REPORT_DIR / "version.json").write_text(json.dumps(outputs["version"], ensure_ascii=False, indent=2), encoding="utf-8")
    DESIGN_MD.write_text(build_design_doc(analysis), encoding="utf-8")


def export_clean_csv(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT draw_date,n1,n2,n3,n4,n5,source FROM draws ORDER BY draw_date").fetchall()
    with (DATA_DIR / "ghana_daywa39_history_clean.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["draw_date", "n1", "n2", "n3", "n4", "n5", "source"])
        writer.writerows(rows)


def run(csv_path: Path, rounds: int, import_only: bool = False) -> dict:
    setup_dirs()
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        run_id = conn.execute(
            "INSERT INTO update_runs(started_at,status,message) VALUES(?,?,?)",
            (stamp(), "running", "start"),
        ).lastrowid
        conn.commit()
        import_result = import_history_csv(conn, csv_path)
        settled_count = settle_predictions(conn)
        export_clean_csv(conn)
        draws = fetch_draws(conn)
        history_before_analysis = settled_history(conn)
        if import_only:
            message = json.dumps({"import": import_result, "settled": settled_count, "draw_count": len(draws)}, ensure_ascii=False)
            conn.execute("UPDATE update_runs SET finished_at=?,status=?,message=? WHERE id=?", (stamp(), "success", message[:1000], run_id))
            conn.commit()
            return {"import": import_result, "settled": settled_count, "draw_count": len(draws)}
        analysis = analyze(draws, rounds=rounds, settled_history_rows=history_before_analysis)
        prediction_status = store_prediction(conn, analysis)
        settled = latest_settled(conn)
        history = settled_history(conn)
        save_outputs(analysis, settled, history)
        message = json.dumps(
            {
                "import": import_result,
                "settled": settled_count,
                "prediction": prediction_status,
                "latest": analysis["latest_draw"]["draw_date"],
                "target": analysis["target_draw_date"],
            },
            ensure_ascii=False,
        )
        conn.execute("UPDATE update_runs SET finished_at=?,status=?,message=? WHERE id=?", (stamp(), "success", message[:1000], run_id))
        conn.commit()
    log(f"done latest={analysis['latest_draw']['draw_date']} top9={fmt_numbers([item['number'] for item in analysis['candidates'][:9]])}")
    return analysis


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ghana Daywa 5/39 Direct precision-spec prediction system")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="History CSV path")
    parser.add_argument("--rounds", type=int, default=120, help="Rolling backtest rounds")
    parser.add_argument("--all", action="store_true", help="Use a deeper 240-round backtest")
    parser.add_argument("--import-only", action="store_true", help="Only import CSV and settle existing predictions")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    rounds = 240 if args.all else max(30, min(360, args.rounds))
    result = run(Path(args.csv), rounds=rounds, import_only=args.import_only)
    if args.import_only:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("戰報：", BATTLE_HTML)
        print("設計書：", DESIGN_MD)
        print("候選Top9：", fmt_numbers([item["number"] for item in result["candidates"][:9]]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
