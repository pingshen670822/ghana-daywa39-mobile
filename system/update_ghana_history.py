#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch official Ghana NLA Daywa 5/39 Direct history and write local CSV."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
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
EFFI_RESULTS_URL = "https://effi-lotto.com/ghana/results/"
LOTTERYNGO_RESULTS_URL = "https://lotteryngo.com/ro/results/ghana/daywa-5-39/"
LOTTERYTEXTS_PAST_RESULTS_URL = "https://lotterytexts.com/ghana/daywa-5-39/past-results/"
LOTTERYTEXTS_AJAX_URL = "https://lotterytexts.com/wp-admin/admin-ajax.php"
LOTTERYTEXTS_LOTTERY_ID = "383"
TAIWAN_TZ = ZoneInfo("Asia/Taipei")
DEFAULT_START_DATE = "2024-04-01"
FULL_SCAN_START_DATE = "2000-01-01"
PREHISTORY_AUDIT_END_DATE = "2024-03-31"
PREHISTORY_SCAN_NOTE = (
    "Official-interface scan from 2000-01-01 through 2024-03-31 "
    "returned zero Daywa 5/39 Direct rows."
)
EN_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
RO_MONTHS = {
    "Ianuarie": 1,
    "Februarie": 2,
    "Martie": 3,
    "Aprilie": 4,
    "Mai": 5,
    "Iunie": 6,
    "Iulie": 7,
    "August": 8,
    "Septembrie": 9,
    "Octombrie": 10,
    "Noiembrie": 11,
    "Decembrie": 12,
}


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


def request_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "user-agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        return response.read().decode("utf-8", "ignore")


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


def official_game_date(official_datetime: str) -> str:
    cleaned = official_datetime.replace("Z", "+00:00")
    dt_utc = datetime.fromisoformat(cleaned)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.date().isoformat()


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
        draw_date=official_game_date(official_datetime),
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


def external_system_date(local_date: datetime) -> str:
    # External Daywa pages publish the game calendar date directly. Keep that
    # date as the canonical draw date and show Taiwan draw time separately.
    return local_date.date().isoformat()


def external_draw(local_date: datetime, numbers: list[int], source_label: str, source_url: str, product_code: str) -> Draw | None:
    if not valid_external_numbers(numbers):
        return None
    return Draw(
        draw_date=external_system_date(local_date),
        n1=sorted(numbers)[0],
        n2=sorted(numbers)[1],
        n3=sorted(numbers)[2],
        n4=sorted(numbers)[3],
        n5=sorted(numbers)[4],
        source=f"{source_label}:{source_url}:ghana_local_date={local_date.date().isoformat()}",
        official_datetime_utc=f"{local_date.date().isoformat()}T19:00:00Z",
        product_code=product_code,
        draw_number="external_verified",
    )


def valid_external_numbers(numbers: list[int]) -> bool:
    return len(numbers) == 5 and len(set(numbers)) == 5 and all(1 <= number <= 39 for number in numbers)


def fetch_effi_direct_results() -> tuple[list[Draw], dict]:
    status = {"source": EFFI_RESULTS_URL, "rows": 0, "direct_rows": 0, "status": "ok", "errors": []}
    try:
        raw = request_text(EFFI_RESULTS_URL)
    except Exception as exc:
        status["status"] = "error"
        status["errors"].append(str(exc))
        return [], status
    draws: list[Draw] = []
    for part in raw.split('<div class="el-result-row">')[1:]:
        row = part.split('<div class="el-result-row">', 1)[0]
        if "5/39 DIRECT" not in row.upper():
            continue
        text = html.unescape(re.sub(r"<[^>]+>", " ", row))
        text = re.sub(r"\s+", " ", text).strip()
        match = re.search(
            r"5/39 DIRECT\s+([A-Z]+).*?([A-Z][a-z]{2})\s+.\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s+((?:\d{2}\s+){4}\d{2})",
            text,
        )
        if not match:
            status["errors"].append(f"unparsed_effi_row:{text[:120]}")
            continue
        product_day = match.group(1).title()
        day = int(match.group(3))
        month = EN_MONTHS.get(match.group(4))
        year = int(match.group(5))
        numbers = [int(part) for part in match.group(6).split()]
        if not month:
            status["errors"].append(f"unknown_month:{match.group(4)}")
            continue
        draw = external_draw(
            datetime(year, month, day),
            numbers,
            "external verified Effi Lotto 5/39 Direct",
            EFFI_RESULTS_URL,
            f"5/39 Direct {product_day} external",
        )
        if draw:
            draws.append(draw)
    status["rows"] = len(draws)
    status["direct_rows"] = len(draws)
    return draws, status


def fetch_lotteryngo_daywa_results() -> tuple[list[Draw], dict]:
    status = {"source": LOTTERYNGO_RESULTS_URL, "rows": 0, "direct_rows": 0, "status": "ok", "errors": []}
    try:
        raw = request_text(LOTTERYNGO_RESULTS_URL)
    except Exception as exc:
        status["status"] = "error"
        status["errors"].append(str(exc))
        return [], status
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"\s+", " ", text)
    start = text.find("Data extragerii Daywa 5/39")
    end = text.find("Daywa 5/39 Numere calde", start if start >= 0 else 0)
    results_text = text[start:end if end > start else None]
    pattern = re.compile(
        r"(luni|Duminic[ăa]|S[âa]mb[ăa]t[ăa]|Vineri|joi|miercuri|mar[ţț]i)\s+"
        r"(Ianuarie|Februarie|Martie|Aprilie|Mai|Iunie|Iulie|August|Septembrie|Octombrie|Noiembrie|Decembrie)\s+"
        r"(\d{2}),\s+(\d{4})\s+((?:\d{2}\s+){4}\d{2})",
        re.IGNORECASE,
    )
    draws: list[Draw] = []
    for match in pattern.finditer(results_text):
        month = RO_MONTHS.get(match.group(2))
        if not month:
            status["errors"].append(f"unknown_month:{match.group(2)}")
            continue
        day = int(match.group(3))
        year = int(match.group(4))
        numbers = [int(part) for part in match.group(5).split()]
        draw = external_draw(
            datetime(year, month, day),
            numbers,
            "external supplemental Lottery n Go Daywa 5/39",
            LOTTERYNGO_RESULTS_URL,
            "Daywa 5/39 supplemental external",
        )
        if draw:
            draws.append(draw)
    status["rows"] = len(draws)
    status["direct_rows"] = len(draws)
    return draws, status


def fetch_lotterytexts_daywa_history(start_year: int = 2019, end_year: int | None = None) -> tuple[list[Draw], dict]:
    end_year = end_year or datetime.now(TAIWAN_TZ).year
    status = {
        "source": LOTTERYTEXTS_PAST_RESULTS_URL,
        "ajax": LOTTERYTEXTS_AJAX_URL,
        "years": [],
        "rows": 0,
        "direct_rows": 0,
        "status": "ok",
        "errors": [],
    }
    try:
        landing = request_text(LOTTERYTEXTS_PAST_RESULTS_URL)
    except Exception as exc:
        status["status"] = "error"
        status["errors"].append(f"landing:{exc}")
        return [], status
    nonce_match = re.search(r"nonce:\s*'([^']+)'", landing)
    if not nonce_match:
        status["status"] = "error"
        status["errors"].append("missing_lotterytexts_nonce")
        return [], status
    nonce = nonce_match.group(1)
    years = sorted({int(year) for year in re.findall(r"<option[^>]+value=['\"](20\d{2})['\"]", landing)}, reverse=True)
    years = [year for year in years if start_year <= year <= end_year] or list(range(end_year, start_year - 1, -1))
    draws_by_date: dict[str, Draw] = {}
    for year in years:
        post_data = urllib.parse.urlencode(
            {
                "action": "past_results_all_ajax",
                "lottery_id": LOTTERYTEXTS_LOTTERY_ID,
                "year": str(year),
                "nonce": nonce,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            LOTTERYTEXTS_AJAX_URL,
            data=post_data,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": LOTTERYTEXTS_PAST_RESULTS_URL,
            },
        )
        raw = ""
        for attempt in range(1, 4):
            try:
                raw = urllib.request.urlopen(request, timeout=60).read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    time.sleep(8 * attempt)
                    continue
                status["errors"].append(f"{year}:HTTP {exc.code} {exc.reason}")
            except Exception as exc:
                status["errors"].append(f"{year}:{exc}")
            break
        if not raw:
            time.sleep(1.25)
            continue
        year_rows = 0
        for section in re.findall(r'<section class="lottery-section lottery-logo">[\s\S]*?</section>', raw):
            date_match = re.search(r"lottery-date[^>]*>\s*([^,<]+),\s*<span>([^<]+)</span>", section)
            numbers = [int(part) for part in re.findall(r"<li>(\d{1,2})</li>", section)]
            if not date_match or len(numbers) != 5:
                continue
            date_text = html.unescape(date_match.group(2)).strip()
            try:
                local_date = datetime.strptime(date_text, "%b %d, %Y")
            except ValueError:
                status["errors"].append(f"unparsed_lotterytexts_date:{date_text}")
                continue
            draw = external_draw(
                local_date,
                numbers,
                "external verified LotteryTexts Daywa 5/39 full history",
                LOTTERYTEXTS_PAST_RESULTS_URL,
                "Daywa 5/39 full-history external",
            )
            if draw:
                draws_by_date[draw.draw_date] = draw
                year_rows += 1
        status["years"].append({"year": year, "rows": year_rows})
        time.sleep(1.25)
    draws = sorted(draws_by_date.values(), key=lambda draw: draw.draw_date)
    status["rows"] = len(draws)
    status["direct_rows"] = len(draws)
    if status["errors"] and not draws:
        status["status"] = "error"
    elif status["errors"]:
        status["status"] = "partial"
    return draws, status


def merge_external_draws(official_draws: list[Draw], external_sets: list[tuple[str, list[Draw], dict]]) -> tuple[list[Draw], dict]:
    by_date = {draw.draw_date: draw for draw in official_draws}
    inserted: list[Draw] = []
    corrected: list[dict] = []
    duplicates = 0
    conflicts = []
    preferred = {"lotterytexts": 0, "lotteryngo": 1, "effi": 2}
    candidates: dict[str, list[tuple[str, Draw]]] = {}
    for source_key, draws, _status in external_sets:
        for draw in draws:
            candidates.setdefault(draw.draw_date, []).append((source_key, draw))
    for draw_date in sorted(candidates):
        options = sorted(candidates[draw_date], key=lambda item: preferred.get(item[0], 9))
        source_key, draw = options[0]
        existing = by_date.get(draw_date)
        draw_numbers = [draw.n1, draw.n2, draw.n3, draw.n4, draw.n5]
        if existing:
            existing_numbers = [existing.n1, existing.n2, existing.n3, existing.n4, existing.n5]
            if existing_numbers == draw_numbers:
                duplicates += 1
            else:
                if "NLA official" in existing.source and "external" in draw.source.lower():
                    conflicts.append(
                        {
                            "draw_date": draw_date,
                            "kept_numbers": existing_numbers,
                            "external_numbers": draw_numbers,
                            "kept_source": existing.source,
                            "external_source": draw.source,
                            "rule": "Official NLA row is preserved; external conflicting row is recorded but not allowed to overwrite official data.",
                        }
                    )
                    continue
                by_date[draw_date] = draw
                corrected.append(
                    {
                        "draw_date": draw_date,
                        "previous_numbers": existing_numbers,
                        "corrected_numbers": draw_numbers,
                        "previous_source": existing.source,
                        "external_source": draw.source,
                        "rule": "Daywa external calendar-date row replaces mismatched 5/39 Direct server-date row.",
                    }
                )
            continue
        by_date[draw_date] = draw
        inserted.append(draw)
    merged = sorted(by_date.values(), key=lambda draw: (draw.draw_date, draw.product_code, draw.draw_number))
    return merged, {
        "status": "applied" if inserted or corrected else "no_new_external_rows",
        "inserted_count": len(inserted),
        "corrected_count": len(corrected),
        "duplicate_confirmations": duplicates,
        "conflict_count": len(conflicts),
        "conflicts": conflicts[:20],
        "inserted_rows": [draw.__dict__ for draw in inserted],
        "corrected_rows": corrected[:20],
        "source_statuses": {source_key: status for source_key, _draws, status in external_sets},
        "rule": "External Daywa rows use the public game calendar date and fill gaps; official NLA rows are preserved when an external source conflicts.",
    }


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


def build_gap_audit(draws: list[Draw], batches: list[dict], prehistory_batches: list[dict] | None = None, external_backfill: dict | None = None) -> dict:
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
        "external_backfill": external_backfill or {},
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
    official_draws, batches = fetch_all(args.start, args.end, args.sleep)
    effi_draws, effi_status = fetch_effi_direct_results()
    lotteryngo_draws, lotteryngo_status = fetch_lotteryngo_daywa_results()
    lotterytexts_draws, lotterytexts_status = fetch_lotterytexts_daywa_history(
        start_year=2019,
        end_year=datetime.strptime(args.end, "%Y-%m-%d").year,
    )
    draws, external_backfill = merge_external_draws(
        official_draws,
        [
            ("lotterytexts", lotterytexts_draws, lotterytexts_status),
            ("effi", effi_draws, effi_status),
            ("lotteryngo", lotteryngo_draws, lotteryngo_status),
        ],
    )
    output = Path(args.output)
    write_csv(draws, output)
    prehistory_batches = scan_prehistory(FULL_SCAN_START_DATE, PREHISTORY_AUDIT_END_DATE, args.audit_sleep) if args.audit_prehistory else previous_prehistory_batches()
    gap_audit = build_gap_audit(official_draws, batches, prehistory_batches, external_backfill)
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
        "official_draw_count": len(official_draws),
        "official_earliest_draw_date": official_draws[0].draw_date if official_draws else None,
        "official_latest_draw_date": official_draws[-1].draw_date if official_draws else None,
        "official_latest_draw": official_draws[-1].__dict__ if official_draws else None,
        "external_backfill": external_backfill,
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
