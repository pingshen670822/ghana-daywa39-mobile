#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch official Ghana NLA Daywa 5/39 Direct history and write local CSV."""

from __future__ import annotations

import argparse
import csv
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OFFICIAL_CSV = DATA_DIR / "ghana_daywa39_history.csv"
RAW_DIR = DATA_DIR / "official_nla_raw"
SUMMARY_JSON = DATA_DIR / "ghana_daywa39_fetch_summary.json"
GAP_AUDIT_JSON = DATA_DIR / "ghana_daywa39_history_gap_audit.json"

SERVER_FN_ID = "a326a1cfceda0eb077997216108eb8dd18bb12e3da7300fd63de2cd7bdcbec2e"
SERVER_FN_URL = f"https://www.nla.com.gh/_serverFn/{SERVER_FN_ID}"
SOURCE_URL = "https://www.nla.com.gh/winning-numbers"
TAIWAN_TZ = ZoneInfo("Asia/Taipei")
DEFAULT_START_DATE = "2024-04-01"
FULL_SCAN_START_DATE = "2000-01-01"
PREHISTORY_AUDIT_END_DATE = "2024-03-31"
PREHISTORY_SCAN_NOTE = (
    "Official-interface scan from 2000-01-01 through 2024-03-31 "
    "returned zero Daywa 5/39 Direct rows."
)


@dataclass(frozen=True)
class Draw:
    draw_date: str
    n1: int
    n2: int
    n3: int
    n4: int
    n5: int
    source: str
    official_datetime_utc: str
    product_code: str
    draw_number: str


def seroval_string(value: str) -> dict:
    return {"t": 1, "s": value}


def seroval_object(ref_id: int, keys: list[str], values: list[dict]) -> dict:
    return {"t": 10, "i": ref_id, "p": {"k": keys, "v": values}, "o": 0}


def build_payload(start_date: str, end_date: str) -> str:
    query = seroval_object(
        0,
        ["data"],
        [
            seroval_object(
                1,
                ["startDate", "endDate"],
                [seroval_string(start_date), seroval_string(end_date)],
            )
        ],
    )
    wrapped = {"t": query, "f": 63, "m": []}
    return json.dumps(wrapped, separators=(",", ":"))


def decode_seroval(node):
    if not isinstance(node, dict) or "t" not in node:
        return node
    tag = node.get("t")
    if tag == 0:
        return int(node.get("s")) if str(node.get("s", "")).isdigit() else float(node.get("s"))
    if tag == 1:
        return node.get("s")
    if tag == 2:
        constants = {
            0: None,
            1: None,
            2: True,
            3: False,
            4: -0.0,
            5: float("inf"),
            6: float("-inf"),
            7: float("nan"),
        }
        return constants.get(int(node.get("s", 1)))
    if tag == 3:
        return False
    if tag == 5:
        return node.get("s")
    if tag == 9:
        return [decode_seroval(item) for item in node.get("a", [])]
    if tag in (10, 11):
        payload = node.get("p", {})
        return {
            key: decode_seroval(value)
            for key, value in zip(payload.get("k", []), payload.get("v", []))
        }
    if tag == 25:
        parsed = decode_seroval(node.get("s", {}))
        return {"error": parsed, "class": node.get("c")}
    return node


def request_range(start_date: str, end_date: str, timeout: int = 45) -> list[dict]:
    payload = build_payload(start_date, end_date)
    url = SERVER_FN_URL + "?" + urllib.parse.urlencode({"payload": payload})
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/x-ndjson, application/json",
            "referer": SOURCE_URL,
            "user-agent": "Mozilla/5.0",
            "x-tsr-serverFn": "true",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        raw = response.read().decode("utf-8")
    decoded = decode_seroval(json.loads(raw))
    error = decoded.get("error") if isinstance(decoded, dict) else None
    if error:
        raise RuntimeError(f"NLA server function error for {start_date}..{end_date}: {error}")
    result = decoded.get("result", {}) if isinstance(decoded, dict) else {}
    data = result.get("data", []) if isinstance(result, dict) else []
    if not isinstance(data, list):
        return []
    return data


def parse_numbers(value: str) -> list[int]:
    try:
        numbers = [int(part.strip()) for part in str(value).split(",") if part.strip()]
    except ValueError:
        return []
    if len(numbers) != 5 or len(set(numbers)) != 5:
        return []
    if any(number < 1 or number > 39 for number in numbers):
        return []
    return sorted(numbers)


def taiwan_date(official_datetime: str) -> str:
    cleaned = official_datetime.replace("Z", "+00:00")
    dt_utc = datetime.fromisoformat(cleaned)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(TAIWAN_TZ).date().isoformat()


def normalize_row(row: dict) -> Draw | None:
    product_code = str(row.get("product_code", "")).strip()
    if "5/39 Direct" not in product_code:
        return None
    numbers = parse_numbers(row.get("results", ""))
    if not numbers:
        return None
    official_datetime = str(row.get("date", "")).strip()
    if not official_datetime:
        return None
    draw_number = str(row.get("draw_number", "")).strip()
    source = f"NLA official winning-numbers:{product_code}:draw#{draw_number}"
    return Draw(
        draw_date=taiwan_date(official_datetime),
        n1=numbers[0],
        n2=numbers[1],
        n3=numbers[2],
        n4=numbers[3],
        n5=numbers[4],
        source=source,
        official_datetime_utc=official_datetime,
        product_code=product_code,
        draw_number=draw_number,
    )


def month_starts(start: datetime, end: datetime):
    cursor = datetime(start.year, start.month, 1)
    stop = datetime(end.year, end.month, 1)
    while cursor <= stop:
        yield cursor
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1)


def month_end(month_start: datetime, final_end: datetime) -> datetime:
    if month_start.month == 12:
        next_month = datetime(month_start.year + 1, 1, 1)
    else:
        next_month = datetime(month_start.year, month_start.month + 1, 1)
    return min(next_month - timedelta(days=1), final_end)


def year_starts(start: datetime, end: datetime):
    cursor = datetime(start.year, 1, 1)
    while cursor <= end:
        yield cursor
        cursor = datetime(cursor.year + 1, 1, 1)


def year_end(year_start: datetime, final_end: datetime) -> datetime:
    return min(datetime(year_start.year, 12, 31), final_end)


def fetch_all(start: str, end: str, sleep_seconds: float = 0.15) -> tuple[list[Draw], list[dict]]:
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    draws: dict[tuple[str, str], Draw] = {}
    batches: list[dict] = []
    for month in month_starts(start_dt, end_dt):
        batch_start = max(month, start_dt).date().isoformat()
        batch_end = month_end(month, end_dt).date().isoformat()
        status = {"start": batch_start, "end": batch_end, "rows": 0, "direct_rows": 0, "status": "ok"}
        try:
            rows = request_range(batch_start, batch_end)
            status["rows"] = len(rows)
            for row in rows:
                draw = normalize_row(row)
                if draw:
                    draws[(draw.product_code, draw.draw_number)] = draw
            status["direct_rows"] = sum(1 for row in rows if "5/39 Direct" in str(row.get("product_code", "")))
        except Exception as exc:
            status["status"] = "error"
            status["error"] = str(exc)
        batches.append(status)
        time.sleep(sleep_seconds)
    return sorted(draws.values(), key=lambda draw: (draw.draw_date, draw.product_code, draw.draw_number)), batches


def product_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        product_code = str(row.get("product_code", "")).strip() or "(blank)"
        counts[product_code] = counts.get(product_code, 0) + 1
    return dict(sorted(counts.items()))


def scan_prehistory(start: str, end: str, sleep_seconds: float = 0.05) -> list[dict]:
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    batches: list[dict] = []
    for year in year_starts(start_dt, end_dt):
        batch_start = max(year, start_dt).date().isoformat()
        batch_end = year_end(year, end_dt).date().isoformat()
        status = {
            "start": batch_start,
            "end": batch_end,
            "rows": 0,
            "direct_rows": 0,
            "product_counts": {},
            "status": "ok",
        }
        try:
            rows = request_range(batch_start, batch_end)
            status["rows"] = len(rows)
            status["direct_rows"] = sum(1 for row in rows if "5/39 Direct" in str(row.get("product_code", "")))
            status["product_counts"] = product_counts(rows)
        except Exception as exc:
            status["status"] = "error"
            status["error"] = str(exc)
        batches.append(status)
        time.sleep(sleep_seconds)
    return batches


def draw_number_gap_summary(draws: list[Draw]) -> dict:
    by_product: dict[str, list[Draw]] = {}
    for draw in draws:
        by_product.setdefault(draw.product_code, []).append(draw)
    products: dict[str, dict] = {}
    minimum_missing_before_public = 0
    missing_inside_public_range = 0
    for product_code, product_draws in sorted(by_product.items()):
        numbered = []
        for draw in product_draws:
            try:
                numbered.append((int(draw.draw_number), draw))
            except (TypeError, ValueError):
                continue
        if not numbered:
            products[product_code] = {
                "captured_draws": len(product_draws),
                "first_draw_date": product_draws[0].draw_date,
                "latest_draw_date": product_draws[-1].draw_date,
                "note": "No numeric official draw_number field was available.",
            }
            continue
        numbered.sort(key=lambda item: item[0])
        draw_numbers = [item[0] for item in numbered]
        min_draw = min(draw_numbers)
        max_draw = max(draw_numbers)
        expected_inside = max_draw - min_draw + 1
        missing_inside = max(0, expected_inside - len(set(draw_numbers)))
        missing_before = max(0, min_draw - 1)
        minimum_missing_before_public += missing_before
        missing_inside_public_range += missing_inside
        products[product_code] = {
            "captured_draws": len(set(draw_numbers)),
            "first_draw_date": numbered[0][1].draw_date,
            "first_visible_draw_number": min_draw,
            "latest_draw_date": numbered[-1][1].draw_date,
            "latest_visible_draw_number": max_draw,
            "minimum_missing_before_public_range": missing_before,
            "missing_inside_public_range": missing_inside,
        }
    return {
        "minimum_missing_before_public_range": minimum_missing_before_public,
        "missing_inside_public_range": missing_inside_public_range,
        "products": products,
    }


def build_gap_audit(draws: list[Draw], batches: list[dict], prehistory_batches: list[dict] | None = None) -> dict:
    prehistory_batches = prehistory_batches or []
    prehistory_direct_rows = sum(int(batch.get("direct_rows") or 0) for batch in prehistory_batches)
    prehistory_rows = sum(int(batch.get("rows") or 0) for batch in prehistory_batches)
    earliest = draws[0].draw_date if draws else None
    latest = draws[-1].draw_date if draws else None
    return {
        "status": "official_public_partial",
        "source": SOURCE_URL,
        "server_function": SERVER_FN_URL,
        "official_public_range": f"{earliest}..{latest}" if earliest and latest else None,
        "official_public_draw_count": len(draws),
        "prehistory_audit_range": f"{FULL_SCAN_START_DATE}..{PREHISTORY_AUDIT_END_DATE}",
        "prehistory_rows": prehistory_rows,
        "prehistory_direct_rows": prehistory_direct_rows,
        "prehistory_status": "no_official_rows_returned" if prehistory_batches and prehistory_direct_rows == 0 else "not_scanned",
        "draw_number_gap_summary": draw_number_gap_summary(draws),
        "batch_count": len(batches),
        "prehistory_batches": prehistory_batches,
        "updated_at_taiwan": datetime.now(TAIWAN_TZ).isoformat(timespec="seconds"),
        "note": (
            "The official public winning-numbers interface exposes 5/39 Direct rows only from "
            f"{earliest or 'unknown'} in the current scan. Earlier draw numbers exist by official draw_number sequence, "
            "but the current public interface did not return their winning numbers."
        ),
    }


def previous_prehistory_batches() -> list[dict]:
    if not GAP_AUDIT_JSON.exists():
        return []
    try:
        existing = json.loads(GAP_AUDIT_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    batches = existing.get("prehistory_batches")
    return batches if isinstance(batches, list) else []


def write_csv(draws: list[Draw], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "draw_date",
                "n1",
                "n2",
                "n3",
                "n4",
                "n5",
                "source",
                "official_datetime_utc",
                "product_code",
                "draw_number",
            ]
        )
        for draw in draws:
            writer.writerow(
                [
                    draw.draw_date,
                    draw.n1,
                    draw.n2,
                    draw.n3,
                    draw.n4,
                    draw.n5,
                    draw.source,
                    draw.official_datetime_utc,
                    draw.product_code,
                    draw.draw_number,
                ]
            )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Ghana NLA Daywa 5/39 Direct history")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", default=(datetime.now(TAIWAN_TZ) + timedelta(days=1)).date().isoformat(), help="End date, YYYY-MM-DD")
    parser.add_argument("--output", default=str(OFFICIAL_CSV), help="Output CSV path")
    parser.add_argument("--sleep", type=float, default=0.15, help="Sleep seconds between monthly requests")
    parser.add_argument("--full-scan", action="store_true", help="Scan from 2000-01-01 instead of the current public range start")
    parser.add_argument("--audit-prehistory", action="store_true", help="Write a yearly official-interface audit for 2000-01-01..2024-03-31")
    parser.add_argument("--audit-sleep", type=float, default=0.05, help="Sleep seconds between prehistory audit yearly requests")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.full_scan and args.start == DEFAULT_START_DATE:
        args.start = FULL_SCAN_START_DATE
        args.audit_prehistory = True
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    draws, batches = fetch_all(args.start, args.end, args.sleep)
    output = Path(args.output)
    write_csv(draws, output)
    prehistory_batches = scan_prehistory(FULL_SCAN_START_DATE, PREHISTORY_AUDIT_END_DATE, args.audit_sleep) if args.audit_prehistory else previous_prehistory_batches()
    gap_audit = build_gap_audit(draws, batches, prehistory_batches)
    GAP_AUDIT_JSON.write_text(json.dumps(gap_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "source": SOURCE_URL,
        "server_function": SERVER_FN_URL,
        "start": args.start,
        "end": args.end,
        "draw_count": len(draws),
        "earliest_draw_date": draws[0].draw_date if draws else None,
        "latest_draw_date": draws[-1].draw_date if draws else None,
        "latest_draw": draws[-1].__dict__ if draws else None,
        "coverage_note": PREHISTORY_SCAN_NOTE,
        "history_gap_audit_json": str(GAP_AUDIT_JSON),
        "history_gap_audit": {
            key: gap_audit.get(key)
            for key in (
                "status",
                "official_public_range",
                "official_public_draw_count",
                "prehistory_audit_range",
                "prehistory_rows",
                "prehistory_direct_rows",
                "prehistory_status",
                "draw_number_gap_summary",
                "note",
            )
        },
        "batches": batches,
        "output_csv": str(output),
        "updated_at_taiwan": datetime.now(TAIWAN_TZ).isoformat(timespec="seconds"),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("draw_count", "earliest_draw_date", "latest_draw_date", "output_csv")}, ensure_ascii=False, indent=2))
    failed = [batch for batch in batches if batch.get("status") != "ok"]
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
