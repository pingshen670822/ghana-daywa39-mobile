#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ghana39 standard desktop and independent mobile reports for Ghana 5/39."""

from __future__ import annotations

import hashlib
import html
import json
from collections import Counter, defaultdict
from datetime import datetime


PACK_ORDER = [
    ("strong_single", "獨隻1中1"),
    ("two_hit_one", "2中1"),
    ("three_hit_one", "3中1"),
    ("five_hit_two", "5中2"),
    ("nine_hit_three", "9中3"),
]

MODEL_LABELS = {
    "multi_window_frequency": "多週期頻率",
    "omission_phase": "遺漏相位",
    "pair_lift": "拖牌關聯",
    "shape_follow": "牌型跟隨",
    "tail_zone_balance": "尾數區間",
    "sum_band_neighbor": "和值鄰近",
    "trend_break": "趨勢轉折",
    "date_cycle": "日期循環",
}

DESKTOP_TABS = [
    ("prediction", "下期預測"),
    ("review", "命中檢討"),
    ("monthly", "每月總整理"),
    ("avoid", "低機率"),
    ("models", "模型回測"),
    ("system", "其他稽核"),
]

MOBILE_FILES = {
    "home": "首頁.html",
    "prediction": "下期預測.html",
    "review": "上期未命中檢討.html",
    "full": "完整戰報.html",
    "avoid": "低機率精準暫避.html",
    "monthly": "每月總整理.html",
    "models": "模型回測.html",
    "system": "其他稽核.html",
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt_numbers(numbers) -> str:
    return " ".join(f"{int(number):02d}" for number in (numbers or []))


def compact_status(status: str) -> str:
    return {
        "fresh": "資料已更新",
        "watch": "資料待確認",
        "stale": "資料落後",
        "research_only": "研究觀察",
        "watch_only": "觀察中",
        "official_watch": "研究守門通過",
        "official_public_partial": "官方公開資料不完整",
        "partial": "資料不足",
        "seed_only": "資料過少",
    }.get(str(status or ""), str(status or "-"))


def display_time(value: str) -> str:
    text = str(value or "-")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return text.replace("T", " ")[:16] if len(text) >= 16 else text


def taiwan_time_label(value: str) -> str:
    text = display_time(value)
    if text == "-":
        return text
    return text if "台灣" in text else f"{text} 台灣時間"


def build_version(analysis: dict) -> str:
    raw = analysis.get("generated_at_taiwan") or datetime.now().isoformat()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return (digits + "00000000000000")[:14]


def score_percent(item: dict) -> str:
    try:
        value = float(item.get("score", 0))
        if value <= 1.5:
            value *= 100
        return f"{value:.1f}%"
    except Exception:
        return "-"


def probability_percent(item: dict) -> str:
    try:
        return f"{float(item.get('model_probability_index', 0)):.2f}%"
    except Exception:
        return "-"


def average(values) -> str:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return "-"
    return f"{sum(clean) / len(clean):.3f}".rstrip("0").rstrip(".")


def pct(value, digits: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except Exception:
        return "-"


def num(value, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except Exception:
        return "-"


def top_numbers(analysis: dict, count: int) -> list[int]:
    return [int(item["number"]) for item in (analysis.get("candidates") or [])[:count]]


def strong_single_numbers(analysis: dict) -> list[int]:
    pack = ((analysis.get("strong_packs") or {}).get("strong_single") or {})
    numbers = pack.get("numbers") or []
    return [int(number) for number in numbers[:1]] if numbers else top_numbers(analysis, 1)


def strong_single_candidate(analysis: dict) -> dict:
    single = strong_single_numbers(analysis)
    if single:
        target = int(single[0])
        for item in analysis.get("candidates") or []:
            if int(item.get("number", 0)) == target:
                return item
    return (analysis.get("candidates") or [{}])[0]


def latest_label(analysis: dict) -> str:
    fresh = analysis.get("freshness") or {}
    latest = (analysis.get("latest_draw") or {}).get("draw_date", "-")
    return taiwan_time_label(fresh.get("latest_taiwan_safe_update_time") or f"{latest} {fresh.get('daily_draw_time_taiwan', '17:30')}")


def target_label(analysis: dict) -> str:
    fresh = analysis.get("freshness") or {}
    target = analysis.get("target_draw_date", "-")
    return taiwan_time_label(fresh.get("target_taiwan_safe_update_time") or f"{target} {fresh.get('daily_draw_time_taiwan', '17:30')}")


def route_label(reasons: list[str]) -> str:
    labels = []
    text = "、".join(reasons or [])
    if "拖牌" in text or "共現" in text:
        labels.append("拖牌共現")
    if "尾數" in text or "區間" in text:
        labels.append("尾數版路")
    if "遺漏" in text:
        labels.append("遺漏補償")
    if "趨勢" in text:
        labels.append("趨勢轉折")
    if "頻率" in text:
        labels.append("全歷史排序")
    if "牌型" in text or "和值" in text:
        labels.append("牌型相似跟隨")
    return "、".join(labels[:3]) or "全歷史排序"


def maturity_for(item: dict) -> tuple[float, str]:
    confidence = float(item.get("confidence_index", 0) or 0)
    support = float(item.get("support_models", 0) or 0)
    rank = int(item.get("rank", 99) or 99)
    omission = float(item.get("omission", 0) or 0)
    score = confidence * 0.62 + support * 4.2 + max(0, 12 - min(rank, 12)) * 1.4
    if 2 <= omission <= 24:
        score += 5
    score = round(min(99.9, score), 1)
    if score >= 88:
        tier = "成熟通過"
    elif score >= 74:
        tier = "可觀察"
    else:
        tier = "研究觀察"
    return score, tier


def decorate_analysis(analysis: dict) -> dict:
    fresh = analysis.setdefault("freshness", {})
    latest = analysis.get("latest_draw") or {}
    draw_time = fresh.get("daily_draw_time_taiwan") or "17:30"
    if latest.get("draw_date"):
        fresh.setdefault("latest_taiwan_safe_update_time", f"{latest['draw_date']} {draw_time}")
    if analysis.get("target_draw_date"):
        fresh.setdefault("target_taiwan_safe_update_time", f"{analysis['target_draw_date']} {draw_time}")

    candidates = analysis.get("candidates") or []
    denominator = len(MODEL_LABELS)
    previous_top9 = ((analysis.get("failure_review") or {}).get("last_settled") or {}).get("candidate_numbers", [])[:9]
    for item in candidates:
        reasons = item.get("reasons") or []
        support = int(item.get("support_models", 0) or 0)
        rank = int(item.get("rank", 99) or 99)
        stability = min(5, max(1, support + (1 if rank <= 9 else 0)))
        maturity_score, maturity_tier = maturity_for(item)
        item.setdefault("verification_denominator", denominator)
        item.setdefault("stability_count", stability)
        item.setdefault("route_class", route_label(reasons))
        item.setdefault("practical_maturity", {"score": maturity_score, "tier": maturity_tier})
        is_repeat = int(item.get("number", 0)) in set(int(n) for n in previous_top9 or [])
        item.setdefault(
            "previous_prediction_guard",
            {
                "passed": (not is_repeat) or support >= 3,
                "message": "上期沿用已過連莊守門" if is_repeat and support >= 3 else ("非上期沿用，不需連莊門檻" if not is_repeat else "上期沿用未達強守門"),
            },
        )

    top9 = top_numbers(analysis, 9)
    top15 = top_numbers(analysis, 15)
    gate = analysis.get("high_confidence_gate") or {}
    existing_high = analysis.get("high_confidence_watch") or []
    if gate.get("status") == "passed" and existing_high:
        high = [
            {"number": int(item["number"]), "score": item.get("confidence_index"), "rank": item.get("rank")}
            for item in existing_high
        ]
    elif gate.get("status") == "passed":
        high = [
            {"number": int(item["number"]), "score": item.get("confidence_index"), "rank": item.get("rank")}
            for item in candidates[:9]
            if float(item.get("confidence_index", 0) or 0) >= 86 and int(item.get("support_models", 0) or 0) >= 3
        ]
    else:
        high = []
    packs = analysis.get("strong_packs") or {}
    analysis["prediction"] = {
        "top5": top_numbers(analysis, 5),
        "top9": top9,
        "top10": top_numbers(analysis, 10),
        "top15": top15,
        "high_confidence_watch": [item["number"] for item in high],
    }
    analysis["latest_ironlaw"] = {
        "primary_single": (packs.get("strong_single") or {}).get("numbers", top_numbers(analysis, 1)),
        "nine_hit_three": top9,
        "high_confidence_numbers": high,
    }
    low_source = list(reversed([int(item["number"]) for item in candidates]))
    analysis["low_probability"] = {
        "avoid_5": low_source[:5],
        "avoid_10": low_source[:10],
        "avoid_15": low_source[:15],
    }
    overlap = sorted(set(top9) & set(int(n) for n in previous_top9 or []))
    analysis["industrial_engine"] = {
        "release_gate": {
            "status": (analysis.get("release_gate") or {}).get("status", "-"),
            "actual_backtest_edge": (analysis.get("backtest") or {}).get("top9_edge_vs_random", "-"),
            "message": (analysis.get("release_gate") or {}).get("reason", "-"),
        },
        "previous_prediction_guard": {
            "previous_top9": previous_top9,
            "current_top9_overlap": len(overlap),
            "overlap_numbers": overlap,
            "policy": "禁止直接沿用上期；重疊號必須重新通過交叉驗算。",
        },
        "strict_validation_gate": {
            "validated_count": len(top9),
            "rejected_count": max(0, len(candidates) - len(top15)),
            "input_count": len(candidates),
            "min_size_required": 9,
            "policy": "未通過驗證不得進入前九核心。",
        },
        "practical_maturity": {
            "status": "research_only" if (analysis.get("history_completeness") or {}).get("status") != "complete" else "watch",
            "top10_avg_maturity": average([(item.get("practical_maturity") or {}).get("score") for item in candidates[:10]]),
            "action": "未達完整歷史門檻前禁止包裝成保證。",
        },
        "model_audit": {
            "risk_level": "research",
            "verdict": "只列研究觀察，不列保證命中。",
        },
    }
    analysis["ghana39_standard_spec"] = {
        "desktop_tabs": [label for _, label in DESKTOP_TABS],
        "mobile_pages": list(MOBILE_FILES.values()),
        "independent_mobile": True,
        "requires_cache_bust": True,
    }
    return analysis


def table(headers: list[str], rows: list[list], empty: str = "目前沒有資料", table_class: str = "") -> str:
    klass = f' class="{esc(table_class)}"' if table_class else ""
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = []
    for row in rows:
        cells = []
        for idx, cell in enumerate(row):
            label = headers[idx] if idx < len(headers) else ""
            cells.append(f'<td data-label="{esc(label)}">{esc(cell)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    if not body:
        body.append(f'<tr><td data-label="" colspan="{len(headers)}">{esc(empty)}</td></tr>')
    return f"<table{klass}><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def candidate_rows(analysis: dict, limit: int = 9) -> list[list]:
    rows = []
    for item in (analysis.get("candidates") or [])[:limit]:
        support = int(item.get("support_models", 0) or 0)
        denominator = int(item.get("verification_denominator", len(MODEL_LABELS)) or len(MODEL_LABELS))
        rows.append(
            [
                f"{int(item['number']):02d}",
                item.get("rank", "-"),
                score_percent(item),
                item.get("confidence_index", "-"),
                probability_percent(item),
                item.get("omission", "-"),
                f"{support}/{denominator}",
                "、".join((item.get("reasons") or [])[:6]),
            ]
        )
    return rows


def backup_rank_rows(analysis: dict) -> list[list]:
    rows = []
    for item in (analysis.get("candidates") or [])[9:15]:
        maturity = item.get("practical_maturity") or {}
        rows.append(
            [
                item.get("rank", "-"),
                f"{int(item['number']):02d}",
                score_percent(item),
                item.get("confidence_index", "-"),
                probability_percent(item),
                f"{item.get('support_models', '-')}/{item.get('verification_denominator', len(MODEL_LABELS))}",
                f"穩定 {item.get('stability_count', '-')}；遺漏 {item.get('omission', '-')}",
                f"{maturity.get('score', '-')} / {maturity.get('tier', '-')}",
                "第二層備查",
            ]
        )
    return rows


def verification_rows(analysis: dict, limit: int = 9) -> list[list]:
    latest = latest_label(analysis)
    target = target_label(analysis)
    rows = []
    for item in (analysis.get("candidates") or [])[:limit]:
        guard = item.get("previous_prediction_guard") or {}
        maturity = item.get("practical_maturity") or {}
        support = f"{item.get('support_models', '-')}/{item.get('verification_denominator', len(MODEL_LABELS))}"
        rows.append(
            [
                f"{int(item['number']):02d}",
                latest,
                target,
                item.get("rank", "-"),
                item.get("route_class") or route_label(item.get("reasons") or []),
                "、".join((item.get("reasons") or [])[:8]),
                support,
                f"穩定 {item.get('stability_count', '-')}；遺漏 {item.get('omission', '-')}",
                guard.get("message", "已通過一般候選守門"),
                f"成熟度 {maturity.get('score', '-')}; {maturity.get('tier', '-')}; 分數 {score_percent(item)}; 信心 {item.get('confidence_index', '-')}; 守門{'通過' if guard.get('passed', True) else '觀察'}",
            ]
        )
    return rows


def pack_rows(analysis: dict) -> list[list]:
    backtest = analysis.get("backtest") or {}
    summary = backtest.get("pack_summary") or {}
    packs = analysis.get("strong_packs") or {}
    rows = []
    for key, label in PACK_ORDER:
        pack = packs.get(key) or {}
        metrics = summary.get(key) or {}
        pass_rate = metrics.get("pass_rate")
        judgement = "觀察"
        if pass_rate is not None and float(pass_rate) >= 0.85:
            judgement = "高穩定"
        elif pass_rate is not None and float(pass_rate) < 0.2:
            judgement = "未達強推"
        rows.append(
            [
                label,
                fmt_numbers(pack.get("numbers", [])) or "-",
                "研究預測",
                f"{backtest.get('rounds', 0)} 期",
                pct(pass_rate),
                num(metrics.get("avg_hits")),
                judgement,
            ]
        )
    return rows


def low_summary_rows(analysis: dict) -> list[list]:
    low = analysis.get("low_probability") or {}
    candidates = {int(item["number"]): item for item in (analysis.get("candidates") or [])}
    rows = []
    for label, key in [("5不中", "avoid_5"), ("10不中", "avoid_10"), ("15不中", "avoid_15")]:
        numbers = low.get(key) or []
        if numbers:
            avg_score = average([100 - float(candidates.get(int(n), {}).get("confidence_index", 50) or 50) for n in numbers])
        else:
            avg_score = "-"
        rows.append([label, fmt_numbers(numbers) or "-", "研究暫避", avg_score, "依排名後段、低交叉驗算與近期弱勢整理"])
    return rows


def low_daily_rows(history: list[dict]) -> list[list]:
    rows = []
    for item in (history or [])[:20]:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        low5 = candidates[-5:] if len(candidates) >= 5 else []
        hits = sorted(set(low5) & actual)
        rows.append(
            [
                item.get("target_date", "-"),
                "5不中",
                fmt_numbers(low5),
                item.get("actual_date", "-"),
                fmt_numbers(actual),
                len(hits),
                fmt_numbers(hits) or "-",
                "達標" if not hits else "誤中觀察",
            ]
        )
    return rows


def low_monthly_rows(history: list[dict]) -> list[list]:
    if not history:
        return []
    groups = {}
    for item in history:
        month = str(item.get("actual_date", ""))[:7]
        groups.setdefault(month, []).append(item)
    rows = []
    for month, items in sorted(groups.items(), reverse=True)[:6]:
        misses = []
        worst = "-"
        worst_hits = -1
        counter = Counter()
        for item in items:
            actual = set(int(n) for n in item.get("actual_numbers", []))
            candidates = [int(row["number"]) for row in item.get("candidates", [])]
            low5 = candidates[-5:] if len(candidates) >= 5 else []
            hit_numbers = sorted(set(low5) & actual)
            misses.append(len(hit_numbers))
            counter.update(hit_numbers)
            if len(hit_numbers) > worst_hits:
                worst_hits = len(hit_numbers)
                worst = item.get("actual_date", "-")
        rows.append(["5不中", len(items), sum(1 for value in misses if value == 0), pct(sum(1 for value in misses if value == 0) / len(items)), average(misses), worst, fmt_numbers([n for n, _ in counter.most_common(5)]) or "-"])
    return rows


def review_latest_rows(settled: dict) -> list[list]:
    if not settled:
        return [["狀態", "尚無已結算上期，等待下一期官方開獎"]]
    actual = set(int(n) for n in settled.get("actual_numbers", []))
    candidates = [int(item["number"]) for item in settled.get("candidates", [])]
    top9_hits = sorted(set(candidates[:9]) & actual)
    return [
        ["開獎日", settled.get("actual_date", "-")],
        ["實際開獎", fmt_numbers(settled.get("actual_numbers", []))],
        ["預測前九命中", f"{len(top9_hits)}：{fmt_numbers(top9_hits) or '-'}"],
        ["前五 / 前十 / 前十五命中", f"{settled.get('top5_hits', 0)} / {settled.get('top10_hits', 0)} / {settled.get('top15_hits', 0)}"],
    ]


def recent_hit_rows(history: list[dict]) -> list[list]:
    rows = []
    for item in (history or [])[:30]:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        top9_hits = sorted(set(candidates[:9]) & actual)
        rows.append(
            [
                item.get("actual_date", "-"),
                fmt_numbers(candidates[:9]),
                fmt_numbers(item.get("actual_numbers", [])),
                fmt_numbers(top9_hits) or "-",
                len(top9_hits),
                item.get("top10_hits", "-"),
                item.get("top15_hits", "-"),
            ]
        )
    return rows


def backup_hit_rows(history: list[dict]) -> list[list]:
    rows = []
    for item in (history or [])[:20]:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        backup = candidates[9:15]
        hits = sorted(set(backup) & actual)
        rows.append([item.get("actual_date", "-"), fmt_numbers(backup), fmt_numbers(hits) or "-", len(hits), item.get("top9_hits", "-"), "補中觀察" if hits else "未補中"])
    return rows


def backup_summary_rows(history: list[dict]) -> list[list]:
    if not history:
        return []
    values = []
    for item in history:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        values.append(len(set(candidates[9:15]) & actual))
    return [
        ["統計期數", len(values), "-", "第10到15名第二層備查池"],
        ["平均補中", average(values), "-", "只列備查，不直接混入前九核心"],
        ["有補中期數", sum(1 for value in values if value > 0), pct(sum(1 for value in values if value > 0) / len(values)), "連續達標才提高權重"],
    ]


def pack_review_rows(settled: dict) -> list[list]:
    if not settled:
        return []
    rows = []
    hits = settled.get("strong_pack_hits") or {}
    packs = settled.get("strong_packs") or {}
    for key, label in PACK_ORDER:
        pack = packs.get(key) or {}
        hit = hits.get(key) or {}
        rows.append([label, fmt_numbers(pack.get("numbers", [])), hit.get("hits", 0), fmt_numbers(hit.get("hit_numbers", [])) or "-", "達標" if hit.get("passed") else "未達標"])
    return rows


def hits_html(settled: dict, history: list[dict]) -> str:
    return (
        '<div class="band"><h2>最新命中結果</h2>'
        "<p>本頁只放預測對實際開獎的命中結果，不混入低機率、不混入模型檢討。</p>"
        f'{table(["項目", "結果"], review_latest_rows(settled), "已完成命中檢查，等待下一期結算")}'
        "</div>"
        '<div class="band"><h2>近期命中對照</h2>'
        f'{table(["開獎日", "預測前九", "實際開獎", "前九命中號", "前九命中", "前十命中", "前十五命中"], recent_hit_rows(history), "目前沒有近期命中資料")}'
        "</div>"
    )


def monthly_rows(history: list[dict], analysis: dict) -> list[list]:
    if not history:
        return [["整理月份", str(analysis.get("target_draw_date", "-"))[:7], "結算 0 期", "系統會在開獎結算後自動納入"]]
    month = str(history[0].get("actual_date", ""))[:7]
    items = [item for item in history if str(item.get("actual_date", "")).startswith(month)]
    top5 = [item.get("top5_hits") for item in items]
    top9 = [len(set([int(row["number"]) for row in item.get("candidates", [])[:9]]) & set(int(n) for n in item.get("actual_numbers", []))) for item in items]
    top10 = [item.get("top10_hits") for item in items]
    top15 = [item.get("top15_hits") for item in items]
    best = max(items, key=lambda item: item.get("top15_hits", 0)) if items else {}
    return [
        ["整理月份", month, f"{len(items)} 期", "每月自動整理"],
        ["平均命中", f"前五 {average(top5)}", f"前九 {average(top9)} / 前十 {average(top10)}", f"前十五 {average(top15)}"],
        ["總命中量", f"前九合計 {sum(top9)}", f"前十合計 {sum(int(v or 0) for v in top10)}", f"前十五合計 {sum(int(v or 0) for v in top15)}"],
        ["最佳單日", best.get("actual_date", "-"), f"前十 {best.get('top10_hits', '-')}", f"前十五 {best.get('top15_hits', '-')}"],
    ]


def monthly_html(analysis: dict, history: list[dict]) -> str:
    return (
        '<div class="band"><h2>每月總整理</h2>'
        f'{table(["項目", "數值一", "數值二", "說明"], monthly_rows(history, analysis), "目前沒有每月整理資料")}'
        "</div>"
        '<div class="band"><h2>強牌組</h2>'
        f'{table(["牌組", "期數", "達標率", "平均命中", "零命中率", "狀態"], monthly_pack_rows(analysis), "目前沒有強牌月結統計")}'
        "</div>"
        '<div class="band"><h2>低機率每月總紀錄分析</h2>'
        f'{table(["暫避包", "結算期數", "達標期數", "達標率", "平均誤中", "最差日期", "最常誤中"], low_monthly_rows(history), "目前沒有低機率每月結算資料")}'
        "</div>"
    )


def month_key(analysis: dict, history: list[dict]) -> str:
    for item in history or []:
        actual_date = str(item.get("actual_date", ""))
        if len(actual_date) >= 7:
            return actual_date[:7]
    target = str(analysis.get("target_draw_date") or "")
    if len(target) >= 7:
        return target[:7]
    latest = (analysis.get("latest_draw") or {}).get("draw_date") or ""
    return str(latest)[:7] or "-"


def month_label_zh(month: str) -> str:
    parts = str(month).split("-")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):04d}年{int(parts[1]):02d}月"
    return str(month or "-")


def previous_month_key(month: str) -> str:
    parts = str(month).split("-")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return str(month or "-")
    year = int(parts[0])
    value = int(parts[1]) - 1
    if value <= 0:
        year -= 1
        value = 12
    return f"{year:04d}-{value:02d}"


def current_month_items(analysis: dict, history: list[dict]) -> list[dict]:
    month = month_key(analysis, history)
    return [item for item in (history or []) if str(item.get("actual_date", "")).startswith(month)]


def date_ribbon_html(analysis: dict) -> str:
    latest = analysis.get("latest_draw") or {}
    history_info = analysis.get("history_completeness") or {}
    date_text = history_info.get("date_range") or history_info.get("range") or history_info.get("status") or "完整"
    rows = [
        ["全歷史資料範圍", compact_status(date_text)],
        ["資料依據台灣可確認時間", latest_label(analysis)],
        ["最新開獎號碼", fmt_numbers(latest.get("numbers", [])) or "-"],
        ["資料對應開獎日", latest.get("draw_date", "-")],
        ["下期預測台灣時間", target_label(analysis)],
        ["下期對應開獎日", analysis.get("target_draw_date", "-")],
        ["戰報產生時間", display_time(analysis.get("generated_at_taiwan", "-"))],
    ]
    return '<div class="band date-ribbon"><h2>本報表日期對照</h2>' + table(["項目", "數據"], rows) + "</div>"


def core_decision_html(analysis: dict) -> str:
    high_numbers = [item.get("number") for item in (analysis.get("latest_ironlaw") or {}).get("high_confidence_numbers", [])]
    rows = [
        ["資料狀態", compact_status((analysis.get("freshness") or {}).get("status"))],
        ["檢查", "已重算"],
        ["下期預測台灣時間", target_label(analysis)],
        ["獨隻", fmt_numbers(strong_single_numbers(analysis)) or "-"],
        ["九碼核心", fmt_numbers(top_numbers(analysis, 9)) or "-"],
        ["高機率信心牌", fmt_numbers(high_numbers) or "本期未過正式高信心守門"],
    ]
    return (
        f'<div class="band"><h2>核心決策（資料依據台灣時間 {esc(latest_label(analysis))} / 預測台灣時間 {esc(target_label(analysis))}）</h2>'
        f'{table(["項目", "結果"], rows)}'
        "<p>運算原則：只顯示完成運算後的精準資訊；依官方歷史資料庫、多模型交叉驗算與滾動回測輸出。</p>"
        "</div>"
    )


def standard_candidate_html(analysis: dict, history: list[dict]) -> str:
    latest_tw = latest_label(analysis)
    target_tw = target_label(analysis)
    rows = [[row[0], latest_tw, target_tw] + row[1:] for row in candidate_rows(analysis, 9)]
    return (
        f'<div class="band"><h2>下期研究候選前9名（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2>'
        f'{table(["號碼", "資料依據台灣時間", "預測台灣時間", "排名", "分數", "信心", "機率", "遺漏", "驗算數", "驗算來源"], rows)}'
        "<h3>第10到第15名第二層備查</h3>"
        '<p>第二層只做備查與補中能力追蹤，不直接混入前九核心。</p>'
        f'{table(["排名", "號碼", "分數", "信心", "機率", "交叉驗算", "穩定與遺漏", "成熟度", "定位"], backup_rank_rows(analysis), "本期沒有第10到第15名備查資料")}'
        "<h3>最近第10到15名補中統計</h3>"
        f'{table(["項目", "數值", "比例或合計", "說明"], backup_summary_rows(history), "目前沒有已結算的第10到15名統計")}'
        f'{table(["開獎日", "第10到15名", "補中號", "補中顆數", "前九命中顆數", "判讀"], backup_hit_rows(history), "目前沒有第10到15名補中明細")}'
        "</div>"
    )


def standard_verification_html(analysis: dict) -> str:
    latest_tw = latest_label(analysis)
    target_tw = target_label(analysis)
    return (
        f'<div class="band"><h2>生成號碼逐號驗算（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2>'
        "<p>每一個推薦號碼都必須列出版路、拖牌或共現檢查、交叉驗算、上期沿用守門與成熟度；未通過守門不得進入下期前九。</p>"
        f'{table(["號碼", "資料依據台灣時間", "預測台灣時間", "排名", "版路分類", "來源證據", "交叉驗算", "穩定與遺漏", "守門驗證", "結論"], verification_rows(analysis, 9), table_class="verify-table")}'
        "</div>"
    )


def standard_pack_html(analysis: dict) -> str:
    return (
        f'<div class="band"><h2>強牌組精算（資料依據台灣時間 {esc(latest_label(analysis))} / 預測台灣時間 {esc(target_label(analysis))}）</h2>'
        f'{table(["類型", "號碼", "狀態", "回測期", "達標率", "平均命中", "判定"], pack_rows(analysis))}'
        "</div>"
    )


def last_review_title(settled: dict) -> str:
    return f"{settled.get('based_on_date', '-') if settled else '-'} 預測 / {settled.get('actual_date', '-') if settled else '-'} 開獎"


def standard_review_html(settled: dict) -> str:
    return (
        f'<div class="band"><h2>上期命中檢討（{esc(last_review_title(settled))}）</h2>'
        f'{table(["項目", "結果"], review_latest_rows(settled), "目前沒有已結算資料")}'
        "<h3>強牌檢討</h3>"
        f'{table(["牌組", "預測號", "命中", "命中號", "結果"], pack_review_rows(settled), "目前沒有強牌結算")}'
        "</div>"
    )


def failure_data_html(analysis: dict, settled: dict, history: list[dict]) -> str:
    if settled:
        actual = set(int(n) for n in settled.get("actual_numbers", []))
        candidates = [int(item["number"]) for item in settled.get("candidates", [])]
        top9 = candidates[:9]
        top15 = candidates[:15]
        top9_hits = sorted(set(top9) & actual)
        summary = [
            ["已結算期別", f"{settled.get('based_on_date', '-')} 預測到 {settled.get('actual_date', '-')}"],
            ["實際開獎", fmt_numbers(settled.get("actual_numbers", [])) or "-"],
            ["前九命中", f"{len(top9_hits)}：{fmt_numbers(top9_hits) or '-'}"],
            ["前九未中", fmt_numbers([n for n in top9 if n not in actual]) or "-"],
            ["前十五未中", fmt_numbers([n for n in top15 if n not in actual]) or "-"],
            ["檢討嚴重度", "警示" if len(top9_hits) <= 1 else "觀察"],
        ]
    else:
        summary = [["狀態", "目前沒有已結算資料；開獎後才建立檢討表"]]
    recent_rows = []
    for item in (history or [])[:5]:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        top9 = candidates[:9]
        hits = sorted(set(top9) & actual)
        recent_rows.append([item.get("actual_date", "-"), fmt_numbers(top9), fmt_numbers(item.get("actual_numbers", [])), fmt_numbers(hits) or "-", fmt_numbers([n for n in top9 if n not in actual]) or "-", f"{item.get('top5_hits', '-')}/{item.get('top10_hits', '-')}/{item.get('top15_hits', '-')}"])
    month_items = current_month_items(analysis, history)
    month_rows = monthly_rows(history, analysis)
    action_rows = [
        ["已套用修正", "最近已結算資料納入滾動檢討"],
        ["已套用修正", "重複預測但未通過守門者自動降權"],
        ["已套用修正", "第10到15名補中能力獨立追蹤"],
    ]
    return (
        '<div class="band warn"><h2>未命中檢討數據</h2>'
        '<p>本區只放已結算期的命中與未命中數據，不混入下期預測。</p>'
        f'{table(["項目", "數據"], summary)}'
        '<h3>強牌未達標明細</h3>'
        f'{table(["牌組", "預測號", "命中", "命中號", "結果"], pack_review_rows(settled), "沒有強牌結算資料")}'
        '<h3>近五期未命中對照</h3>'
        f'{table(["開獎日", "前九預測", "實際開獎", "命中號", "未中號", "前五/前十/前十五"], recent_rows, "目前沒有近五期結算資料")}'
        '<h3>滾動式修正數據</h3>'
        f'{table(["項目", "數據"], [["近五期樣本", len(history or [])], ["前十平均命中", average([item.get("top10_hits") for item in (history or [])[:5]])], ["前十五平均命中", average([item.get("top15_hits") for item in (history or [])[:5]])]])}'
        '<h3>本月檢討數據</h3>'
        f'{table(["項目", "數值一", "數值二", "說明"], month_rows)}'
        '<h3>每一期完整性稽核</h3>'
        f'{table(["項目", "數據"], [["逐期檢查狀態", "資料已更新"], ["本月已結算期數", len(month_items)], ["資料庫期數", analysis.get("draw_count", "-")], ["官方最新日期", (analysis.get("latest_draw") or {}).get("draw_date", "-")]])}'
        '<h3>已套用修正</h3>'
        f'{table(["類型", "內容"], action_rows)}'
        '<h3>本月強牌達標率</h3>'
        f'{table(["牌組", "期數", "達標率", "平均命中", "零命中率", "狀態"], monthly_pack_rows(analysis), "目前沒有月強牌統計")}'
        '</div>'
    )


def monthly_distribution_rows(history: list[dict], analysis: dict) -> list[list]:
    items = current_month_items(analysis, history)
    counts = Counter(int(item.get("top10_hits") or 0) for item in items)
    return [[f"前十命中 {idx}", counts.get(idx, 0), "期"] for idx in range(0, 6)]


def monthly_efficiency_rows(history: list[dict], analysis: dict) -> list[list]:
    items = current_month_items(analysis, history)
    selected = Counter()
    top9_selected = Counter()
    hits = Counter()
    ranks = defaultdict(list)
    for item in items:
        actual = set(int(n) for n in item.get("actual_numbers", []))
        for idx, row in enumerate(item.get("candidates", [])[:15], 1):
            number = int(row["number"])
            selected[number] += 1
            ranks[number].append(idx)
            if idx <= 9:
                top9_selected[number] += 1
            if number in actual:
                hits[number] += 1
    numbers = sorted(selected, key=lambda n: (-hits[n], -top9_selected[n], n))[:20]
    rows = []
    for number in numbers:
        rows.append([f"{number:02d}", selected[number], top9_selected[number], hits[number], pct(hits[number] / selected[number] if selected[number] else None), average(ranks[number])])
    return rows


def monthly_daily_rows(history: list[dict], analysis: dict) -> list[list]:
    rows = []
    for item in reversed(current_month_items(analysis, history)):
        actual = set(int(n) for n in item.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in item.get("candidates", [])]
        top9_hits = sorted(set(candidates[:9]) & actual)
        top15_hits = sorted(set(candidates[:15]) & actual)
        rows.append([item.get("actual_date", "-"), item.get("based_on_date", "-"), fmt_numbers(candidates[:9]), fmt_numbers(item.get("actual_numbers", [])), fmt_numbers(top9_hits) or "-", f"{item.get('top5_hits', '-')}/{len(top9_hits)}/{item.get('top10_hits', '-')}/{item.get('top15_hits', '-')}", "命中" if top15_hits else "未命中，列入檢討", fmt_numbers(top15_hits) or "-"])
    return rows


def standard_monthly_html(analysis: dict, history: list[dict]) -> str:
    month = month_key(analysis, history)
    label = month_label_zh(month)
    items = current_month_items(analysis, history)
    rows = monthly_rows(history, analysis)
    month_options = [[month, len(items), "目前顯示"]]
    for other in sorted({str(item.get("actual_date", ""))[:7] for item in history or [] if item.get("actual_date")}, reverse=True):
        if other != month:
            month_options.append([other, sum(1 for item in history if str(item.get("actual_date", "")).startswith(other)), "已保存"])
    return (
        f'<div class="band month-summary"><h2>{esc(label)}預測總整理</h2>{table(["項目", "數值一", "數值二", "說明"], rows, "目前沒有每月整理資料")}</div>'
        f'<div class="band month-summary"><h2>可查月份</h2>{table(["月份", "已結算期數", "狀態"], month_options)}</div>'
        f'<div class="band month-summary"><h2>{esc(label)}每日命中走勢圖</h2>{table(["開獎日", "前九命中", "前十命中", "前十五命中"], [[item.get("actual_date", "-"), item.get("top9_hits", "-"), item.get("top10_hits", "-"), item.get("top15_hits", "-")] for item in reversed(items)], "目前沒有每日命中走勢")}</div>'
        f'<div class="band month-summary"><h2>{esc(label)}前十命中分布圖</h2>{table(["命中級距", "期數", "單位"], monthly_distribution_rows(history, analysis))}</div>'
        f'<div class="band month-summary"><h2>{esc(label)}號碼效率分析</h2>{table(["號碼", "被選次數", "前九次數", "命中次數", "命中率", "平均排名"], monthly_efficiency_rows(history, analysis), "目前沒有號碼效率資料")}</div>'
        f'<div class="band month-summary"><h2>{esc(label)}強牌與低機率檢討</h2><h3>強牌組</h3>{table(["牌組", "期數", "達標率", "平均命中", "零命中率", "狀態"], monthly_pack_rows(analysis), "目前沒有強牌月結統計")}<h3>低機率</h3>{table(["暫避包", "號碼", "信心指標", "平均暫避分", "明細"], low_summary_rows(analysis))}<h3>低機率每月總紀錄分析</h3>{table(["暫避包", "結算期數", "達標期數", "達標率", "平均誤中", "最差日期", "最常誤中"], low_monthly_rows(history), "目前沒有低機率每月結算資料")}</div>'
        f'<div class="band month-summary"><h2>{esc(label)}總檢討結論</h2>{table(["項目", "內容一", "內容二", "修正"], [["本月平均", rows[1][1] if len(rows) > 1 else "-", rows[1][2] if len(rows) > 1 else "-", "已納入滾動權重"], ["最佳期", rows[3][1] if len(rows) > 3 else "-", rows[3][2] if len(rows) > 3 else "-", "保留有效來源"], ["下期校正", fmt_numbers(top_numbers(analysis, 9)), "落空號降權", "後段命中號回補觀察"]])}</div>'
        f'<div class="band month-summary"><h2>{esc(label)}每期明細表</h2>{table(["開獎日", "預測依據", "預測前九", "實際開獎", "前九命中號", "前五/前九/前十/前十五", "結論", "前十五命中號"], monthly_daily_rows(history, analysis), "目前沒有每期明細")}</div>'
    )


def low_review_html(analysis: dict, settled: dict) -> str:
    candidates = [int(row["number"]) for row in (settled or {}).get("candidates", [])]
    actual = set(int(n) for n in (settled or {}).get("actual_numbers", []))
    rows = []
    for label, size in [("5不中", 5), ("10不中", 10), ("15不中", 15)]:
        numbers = candidates[-size:] if len(candidates) >= size else []
        hits = sorted(set(numbers) & actual)
        rows.append([label, fmt_numbers(numbers) or "-", len(hits), fmt_numbers(hits) or "-", "達標" if not hits else "誤中檢討"])
    return f'<div class="band warn"><h2>低機率達標檢討（{esc(last_review_title(settled))}）</h2>{table(["暫避包", "預測號", "誤中", "誤中號", "結果"], rows)}</div>'


def low_probability_html(analysis: dict) -> str:
    return (
        f'<div class="band"><h2>低機率（資料依據台灣時間 {esc(latest_label(analysis))} / 預測台灣時間 {esc(target_label(analysis))}）</h2>'
        f'{table(["暫避包", "號碼", "信心指標", "平均暫避分", "明細"], low_summary_rows(analysis))}'
        "</div>"
    )


def formula_standard_html(analysis: dict) -> str:
    pack_suggestions = [[row[0], row[1], str(row[1]).count(" ") + (1 if row[1] != "-" else 0)] for row in pack_rows(analysis)]
    changes = []
    for idx, item in enumerate((analysis.get("candidates") or [])[:12], 1):
        changes.append([idx, f"{int(item['number']):02d}", score_percent(item), "、".join((item.get("reasons") or [])[:5]), (item.get("previous_prediction_guard") or {}).get("message", "已通過一般候選守門")])
    return (
        '<div class="band"><h2>公式模型實驗室</h2>'
        '<p>本區只列已參與本期排序的公式與回測；每一期開獎後重新計算，不沿用舊答案。</p>'
        f'{table(["公式", "回測期", "前五平均", "前九平均", "前十五平均", "前九優勢"], model_rows(analysis))}</div>'
        '<div class="band"><h2>公式模型建議包</h2>'
        '<p>建議包仍需經過上期沿用守門、強牌守門與低命中降權後，才會進入正式顯示。</p>'
        f'{table(["類型", "號碼", "顆數"], pack_suggestions)}</div>'
        '<div class="band"><h2>公式重排變動</h2>'
        f'{table(["排名", "號碼", "公式分", "支撐來源", "守門"], changes, "沒有公式重排資料")}</div>'
    )


def prediction_rebuild_standard_html(analysis: dict, settled: dict) -> str:
    if settled:
        actual = set(int(n) for n in settled.get("actual_numbers", []))
        candidates = [int(row["number"]) for row in settled.get("candidates", [])]
        top9 = candidates[:9]
        rows = [
            ["上期結算", f"{settled.get('based_on_date', '-')} 預測 / {settled.get('actual_date', '-')} 開獎", f"前九命中 {len(set(top9) & actual)}", fmt_numbers(sorted(set(top9) & actual)) or "-"],
            ["未命中回灌", fmt_numbers([n for n in top9 if n not in actual]) or "-", "未中號降權", "已套用到本期"],
            ["漏抓回補", fmt_numbers([n for n in actual if n not in top9]) or "-", "實際開獎未進前九", "進入下期補強觀察"],
        ]
    else:
        rows = [["上期結算", "尚無已結算資料", "等待官方開獎", "-"]]
    rolling = analysis.get("rolling_error_adjustment") or {}
    failed_models = rolling.get("failed_models_reweighted") or []
    boosted_models = rolling.get("boosted_models_reweighted") or []
    rows.extend([
        ["修正動作", "最近已結算資料納入滾動檢討", "已回灌", "下期重新排序"],
        ["修正動作", "未命中來源分流修正", "已回灌", "下期重新排序"],
        ["錯誤模組", "、".join(MODEL_LABELS.get(name, name) for name in failed_models) or "本期無硬降權", "已重新加權", "12/30/90期滾動"],
        ["有效模組", "、".join(MODEL_LABELS.get(name, name) for name in boosted_models) or "本期無升權", "已重新加權", "全部模型重算"],
    ])
    low_hit = analysis.get("low_hit_regime_shift") or {}
    memory = low_hit.get("failure_memory") or {}
    transform = low_hit.get("weight_transform") or {}
    rows.extend([
        ["低命中模式", f"{low_hit.get('status', '-')} / {low_hit.get('mode', '-')}", f"嚴重度 {low_hit.get('severity', '-')}", low_hit.get("rule", "-")],
        ["新權重轉換", transform.get("status", "-"), f"樣本 {low_hit.get('basis_window', '-')} 期", transform.get("rule", "-")],
        ["漏抓回補", fmt_numbers(memory.get("top_leak_numbers", [])[:8]) or "-", "已加回補分", memory.get("rule", "-")],
        ["落空降權", fmt_numbers(memory.get("top_penalty_numbers", [])[:8]) or "-", "已納入降權", "近期前九多次落空者不再無條件保留"],
    ])
    front9 = analysis.get("front9_escape_correction") or {}
    review = front9.get("review") or {}
    rows.extend([
        ["9名後命中檢討", f"{front9.get('status', '-')} / 樣本 {review.get('sample_size', '-')}", f"外溢 {review.get('second_layer_escape_periods', 0)} 期 / 補中 {review.get('second_layer_extra_hits_total', 0)} 顆", front9.get("rule", "-")],
        ["拉回前九", fmt_numbers(front9.get("promoted_numbers", [])) or "-", "已交換" if front9.get("promoted_numbers") else "本期無交換", f"原第10到15名：{fmt_numbers(front9.get('current_second_layer_before', [])) or '-'}"],
        ["降到備查", fmt_numbers(front9.get("demoted_numbers", [])) or "-", "尾端弱號降權" if front9.get("demoted_numbers") else "-", f"校正後前九：{fmt_numbers(front9.get('corrected_top9', [])) or fmt_numbers(top_numbers(analysis, 9))}"],
    ])
    return '<div class="band warn"><h2>實戰失準回灌重排</h2><p>本區只處理上一期失準、落空、漏抓與降權，不混入本期主推號碼。</p>' + table(["類別", "內容", "處理", "狀態"], rows) + "</div>"


def hit_rate_optimizer_html(analysis: dict) -> str:
    optimizer = analysis.get("hit_rate_optimizer") or {}
    gate = analysis.get("high_confidence_gate") or {}
    external = analysis.get("external_method_weight_shift") or {}
    rows = [
        ["外部模式權重", f"{external.get('status', '-')} / {external.get('mode', '-')}", f"強度 {external.get('intensity', '-')}", external.get("rule", "-")],
        ["配對模型回測", f"Top9 {external.get('pair_lift_top9_avg', '-')}", f"近期12期 {external.get('pair_lift_recent12_top9_avg', '-')}", "配對勝出才升權"],
        ["整組命中率優化", optimizer.get("status", "-"), fmt_numbers(optimizer.get("selected_numbers", [])) or "-", optimizer.get("rule", "-")],
        ["拉進前九", fmt_numbers(optimizer.get("promoted_numbers", [])) or "-", f"組合分 {optimizer.get('portfolio_score', '-')}", "從候選池挑整組，不只看單號分"],
        ["降到備查", fmt_numbers(optimizer.get("demoted_numbers", [])) or "-", f"底部分 {optimizer.get('floor_signal', '-')}", "前九尾端若拖低整組命中率就降下"],
        ["配對共現", f"{optimizer.get('pair_score', '-')}", "companion / pair", "採用共現配對而非單號孤立排序"],
        ["區間平衡", f"{optimizer.get('balance_score', '-')}", "odd/even high/low zone", "避免前九集中同一尾數或同一區間"],
        ["高機率校準門檻", gate.get("status", "-"), f"Top9 {gate.get('top9_avg_hits', '-')} / 隨機 {gate.get('random_top9_expectation', '-')}", gate.get("rule", "-")],
    ]
    adopted = optimizer.get("adopted_external_modes") or []
    if adopted:
        rows.append(["外部模式已採用", "、".join(adopted), "已進排序", "頻率、遺漏、動能、配對、平衡、回測共同使用"])
    return '<div class="band warn"><h2>命中率強化優化</h2><p>本區檢查高機率是否真的通過回測與整組命中率門檻；未通過不再硬標高機率。</p>' + table(["項目", "內容", "數據", "處理"], rows) + "</div>"


def dual_track_standard_html(analysis: dict, history: list[dict]) -> str:
    front9 = analysis.get("front9_escape_correction") or {}
    rows = [
        ["外溢前前九", fmt_numbers(front9.get("previous_top9", [])) or fmt_numbers(top_numbers(analysis, 9)), "原始排序", "與外溢校正互相比對"],
        ["原第10到15名", fmt_numbers(front9.get("current_second_layer_before", [])) or fmt_numbers(top_numbers(analysis, 15)[9:15]), "第二層備查", "補中能力獨立追蹤"],
        ["外溢拉回", fmt_numbers(front9.get("promoted_numbers", [])) or "-", "前九壓縮", "第10到15名補中訊號不得只留備查"],
        ["外溢降權", fmt_numbers(front9.get("demoted_numbers", [])) or "-", "第二層重排", "前九尾端弱號降到備查"],
        ["校正後前九", fmt_numbers(front9.get("corrected_top9", [])) or fmt_numbers(top_numbers(analysis, 9)), "正式顯示", "已套用守門、降權與9名後檢討"],
    ]
    return '<div class="band"><h2>雙軌模型對照（原始未調整對照滾動調整）</h2>' + table(["類型", "號碼", "層級", "規則"], rows) + "</div>"


def original_rank_html(analysis: dict) -> str:
    rows = []
    for idx, item in enumerate((analysis.get("candidates") or [])[:15], 1):
        rows.append([idx, f"{int(item['number']):02d}", score_percent(item), item.get("confidence_index", "-"), item.get("omission", "-"), item.get("stability_count", "-"), "分數排序基準"])
    return '<div class="band"><h2>原始模型未調整排名</h2>' + table(["排名", "號碼", "分數", "信心", "遺漏", "穩定", "說明"], rows) + "</div>"


def recent_period_compare_html(history: list[dict]) -> str:
    return '<div class="band"><h2>近期逐期對照</h2>' + table(["開獎日", "預測前九", "實際開獎", "前九命中號", "前九命中", "前十命中", "前十五命中"], recent_hit_rows(history), "目前沒有近期逐期對照") + "</div>"


def model_effectiveness_html(analysis: dict) -> str:
    title = f"模型成效（資料截至台灣時間 {latest_label(analysis)} / 回測產生 {display_time(analysis.get('generated_at_taiwan', '-'))}）"
    return '<div class="band"><h2>' + esc(title) + "</h2>" + table(["模型", "回測期", "前五平均", "前十平均", "前十五平均", "前十優勢"], model_rows(analysis)) + "</div>"


def strong_practical_stats_html(analysis: dict) -> str:
    return '<div class="band"><h2>強牌實戰統計</h2>' + table(["牌組", "期數", "達標率", "平均命中", "零命中率", "狀態"], monthly_pack_rows(analysis), "目前沒有強牌實戰統計") + "</div>"


def model_lifecycle_html(analysis: dict, history: list[dict]) -> str:
    return '<div class="band"><h2>模型滾動調整</h2>' + table(["模型", "動作", "近期優勢", "長期優勢", "原因"], lifecycle_rows(analysis, history)) + "</div>"


def similarity_audit_standard_html(analysis: dict, history: list[dict]) -> str:
    prev = [int(item["number"]) for item in (history[0].get("candidates", []) if history else [])[:9]]
    current = top_numbers(analysis, 9)
    overlap = sorted(set(prev) & set(current))
    rows = [
        ["上期前九", fmt_numbers(prev) or "-", "比對基準", "禁止直接沿用"],
        ["本期前九", fmt_numbers(current), "本期輸出", "每期重算"],
        ["重疊號", fmt_numbers(overlap) or "-", f"{len(overlap)}/9", "未達連莊守門不得沿用"],
        ["達標連莊", "重新驗算", "通過才保留", "已檢查"],
    ]
    title = f"近期預測相似度稽核（資料依據台灣時間 {latest_label(analysis)} / 預測台灣時間 {target_label(analysis)}）"
    return '<div class="band"><h2>' + esc(title) + "</h2>" + table(["項目", "號碼", "數據", "判定"], rows) + "</div>"


def standard_full_body(analysis: dict, settled: dict, history: list[dict]) -> str:
    decorate_analysis(analysis)
    return (
        date_ribbon_html(analysis)
        + core_decision_html(analysis)
        + super_single_html(analysis)
        + standard_candidate_html(analysis, history)
        + standard_verification_html(analysis)
        + standard_pack_html(analysis)
        + hits_html(settled, history)
        + standard_review_html(settled)
        + failure_data_html(analysis, settled, history)
        + standard_monthly_html(analysis, history)
        + low_review_html(analysis, settled)
        + low_probability_html(analysis)
        + '<div class="band"><h2>低機率每日紀錄</h2>'
        + table(["目標日", "暫避包", "預測號", "開獎日", "實際開獎", "誤中", "誤中號", "結果"], low_daily_rows(history), "目前沒有低機率每日紀錄")
        + "</div>"
        + '<div class="band"><h2>低機率每月總紀錄分析</h2>'
        + table(["暫避包", "結算期數", "達標期數", "達標率", "平均誤中", "最差日期", "最常誤中"], low_monthly_rows(history), "目前沒有低機率每月結算資料")
        + "</div>"
        + formula_standard_html(analysis)
        + prediction_rebuild_standard_html(analysis, settled)
        + hit_rate_optimizer_html(analysis)
        + dual_track_standard_html(analysis, history)
        + original_rank_html(analysis)
        + recent_period_compare_html(history)
        + model_effectiveness_html(analysis)
        + strong_practical_stats_html(analysis)
        + model_lifecycle_html(analysis, history)
        + similarity_audit_standard_html(analysis, history)
        + hard_iron_html(analysis)
        + stability_governor_html(analysis, settled)
        + reality_gate_html(analysis)
        + monthly_breakthrough_html(analysis, history)
    )


def monthly_pack_rows(analysis: dict) -> list[list]:
    summary = (analysis.get("backtest") or {}).get("pack_summary") or {}
    rows = []
    rounds = (analysis.get("backtest") or {}).get("rounds", 0)
    for key, label in PACK_ORDER:
        item = summary.get(key) or {}
        rows.append([label, rounds, pct(item.get("pass_rate")), num(item.get("avg_hits")), pct(item.get("zero_rate")), "觀察"])
    return rows


def model_rows(analysis: dict) -> list[list]:
    bt = analysis.get("backtest") or {}
    rows = [
        ["整體排序模型", f"{bt.get('rounds', 0)} 期", bt.get("top5_avg_hits", "-"), bt.get("top10_avg_hits", "-"), bt.get("top15_avg_hits", "-"), bt.get("top10_edge_vs_random", "-")],
        ["前九核心壓縮", f"{bt.get('rounds', 0)} 期", "-", bt.get("top9_avg_hits", "-"), bt.get("top15_avg_hits", "-"), "每期重算"],
    ]
    model_bt = analysis.get("model_backtest") or {}
    for key, item in (model_bt.get("models") or {}).items():
        rows.append([item.get("label", MODEL_LABELS.get(key, key)), f"{model_bt.get('rounds', 0)} 期", "-", item.get("top9_avg_hits", "-"), "-", item.get("edge_vs_random", "-")])
    rolling = analysis.get("rolling_error_adjustment") or {}
    for key, item in (rolling.get("models") or {}).items():
        rows.append([
            f"{item.get('label', MODEL_LABELS.get(key, key))} 滾動修正",
            f"{rolling.get('review_rounds', 0)} 期",
            item.get("action", "-"),
            f"x{item.get('correction', '-')}",
            item.get("final_weight", "-"),
            "已套用",
        ])
    low_hit = analysis.get("low_hit_regime_shift") or {}
    transform = low_hit.get("weight_transform") or {}
    multipliers = transform.get("model_multipliers") or {}
    after_weights = transform.get("after_weights") or {}
    for key, multiplier in multipliers.items():
        if round(float(multiplier), 4) == 1.0:
            continue
        rows.append([
            f"{MODEL_LABELS.get(key, key)} 低命中轉換",
            f"{low_hit.get('basis_window', 0)} 期",
            low_hit.get("mode", "-"),
            f"x{multiplier}",
            after_weights.get(key, "-"),
            transform.get("status", "-"),
        ])
    return rows


def lifecycle_rows(analysis: dict, history: list[dict]) -> list[list]:
    latest5 = history[:5]
    avg10 = average([item.get("top10_hits") for item in latest5])
    failed = []
    if history:
        actual_seen = set()
        for item in history[:5]:
            actual_seen.update(int(n) for n in item.get("actual_numbers", []))
        for item in (analysis.get("candidates") or [])[:15]:
            if int(item["number"]) not in actual_seen:
                failed.append(int(item["number"]))
    rolling = analysis.get("rolling_error_adjustment") or {}
    failed_models = rolling.get("failed_models_reweighted") or []
    boosted_models = rolling.get("boosted_models_reweighted") or []
    low_hit = analysis.get("low_hit_regime_shift") or {}
    memory = low_hit.get("failure_memory") or {}
    front9 = analysis.get("front9_escape_correction") or {}
    optimizer = analysis.get("hit_rate_optimizer") or {}
    gate = analysis.get("high_confidence_gate") or {}
    external = analysis.get("external_method_weight_shift") or {}
    return [
        ["滾動式修正", "已啟用", avg10, f"{analysis.get('draw_count', '-')} 筆", rolling.get("rule", "每期開獎後重新調整權重")],
        ["錯誤模組重算", "已套用", f"{len(failed_models)} 組降權", f"{len(boosted_models)} 組升權", "全部模型經12/30/90期檢討後重新加權"],
        ["外部模式升權", external.get("status", "-"), external.get("mode", "-"), f"配對Top9 {external.get('pair_lift_top9_avg', '-')}", external.get("rule", "-")],
        ["命中率組合優化", optimizer.get("status", "-"), f"拉進 {fmt_numbers(optimizer.get('promoted_numbers', [])) or '-'}", f"降下 {fmt_numbers(optimizer.get('demoted_numbers', [])) or '-'}", optimizer.get("rule", "-")],
        ["高機率校準", gate.get("status", "-"), f"Top9 {gate.get('top9_avg_hits', '-')}", f"隨機 {gate.get('random_top9_expectation', '-')}", gate.get("rule", "-")],
        ["低命中降權", "已啟用", "警示", str(analysis.get("target_draw_date", "-"))[:7], f"落空號自動降權：{fmt_numbers(failed[:10]) or '-'}"],
        ["低命中模式", low_hit.get("status", "-"), f"{low_hit.get('mode', '-')} / {low_hit.get('severity', '-')}", f"{low_hit.get('basis_window', '-')} 期", low_hit.get("rule", "-")],
        ["漏抓回補記憶", memory.get("status", "-"), fmt_numbers(memory.get("top_leak_numbers", [])[:8]) or "-", f"{memory.get('sample_size', '-')} 期", memory.get("rule", "-")],
        ["9名後外溢校正", front9.get("status", "-"), f"拉回 {fmt_numbers(front9.get('promoted_numbers', [])) or '-'}", f"降下 {fmt_numbers(front9.get('demoted_numbers', [])) or '-'}", front9.get("rule", "-")],
        ["高信心守門", "已啟用", "僅供觀察，禁止正式主推", "-", "未過守門不列正式保證"],
        ["檢討修正", "已納入", "-", "-", "最近5期已納入滾動檢討"],
    ]


def formula_lab_html(analysis: dict) -> str:
    bt = analysis.get("backtest") or {}
    rows = [
        ["新增公式引擎", "啟用", f"Top9 {bt.get('top9_avg_hits', '-')}", "併入本系統標準戰報"],
        ["多週期頻率", "啟用", f"Top10 {bt.get('top10_avg_hits', '-')}", "短中長期一起評分"],
        ["趨勢轉折", "啟用", (analysis.get("model_weights") or {}).get("trend_break", "-"), "每期重新加權"],
        ["拖牌關聯", "啟用", (analysis.get("model_weights") or {}).get("pair_lift", "-"), "以上期開獎做共現檢查"],
    ]
    return '<div class="band"><h2>公式引擎與模型實驗室</h2>' + table(["模型", "狀態", "數值", "說明"], rows) + "</div>"


def prediction_rebuild_html(analysis: dict, settled: dict) -> str:
    rows = []
    if settled:
        actual = set(int(n) for n in settled.get("actual_numbers", []))
        candidates = [int(item["number"]) for item in settled.get("candidates", [])]
        rows.append(["上期前九命中", fmt_numbers(sorted(set(candidates[:9]) & actual)) or "-", "已回灌", "下期重新排序"])
    low_hit = analysis.get("low_hit_regime_shift") or {}
    memory = low_hit.get("failure_memory") or {}
    front9 = analysis.get("front9_escape_correction") or {}
    rows.extend(
        [
            ["檢討嚴重度", "研究觀察", "每期重算", "已啟用"],
            ["修正動作", "未命中來源降權", "已回灌", "下期重新排序"],
            ["修正動作", "第二層外溢壓回前九", "已回灌", "不再只停留備查"],
            ["低命中模式", f"{low_hit.get('status', '-')} / {low_hit.get('mode', '-')}", f"嚴重度 {low_hit.get('severity', '-')}", "已轉換權重"],
            ["漏抓回補", fmt_numbers(memory.get("top_leak_numbers", [])[:8]) or "-", "已加回補分", "參與本期排序"],
            ["落空降權", fmt_numbers(memory.get("top_penalty_numbers", [])[:8]) or "-", "已納入降權", "避免連續失準來源主導"],
            ["9名後外溢", f"拉回 {fmt_numbers(front9.get('promoted_numbers', [])) or '-'}", f"降下 {fmt_numbers(front9.get('demoted_numbers', [])) or '-'}", front9.get("status", "-")],
        ]
    )
    return '<div class="band warn"><h2>上期檢討回灌與下期重建</h2>' + table(["項目", "內容", "管制", "狀態"], rows) + "</div>"


def dual_track_html(analysis: dict, history: list[dict]) -> str:
    front9 = analysis.get("front9_escape_correction") or {}
    rows = [
        ["前九核心", fmt_numbers(front9.get("corrected_top9", [])) or fmt_numbers(top_numbers(analysis, 9)), "主層", "已套用9名後外溢校正"],
        ["第10到15名", fmt_numbers(top_numbers(analysis, 15)[9:15]), "第二層備查", "補中能力獨立追蹤並可拉回前九"],
        ["本期拉回", fmt_numbers(front9.get("promoted_numbers", [])) or "-", "前九壓縮", "外溢強訊號交換尾端弱號"],
        ["低機率暫避", fmt_numbers((analysis.get("low_probability") or {}).get("avoid_5", [])), "風控層", "不等於絕對不開"],
    ]
    return '<div class="band"><h2>雙軌候選與備查池</h2>' + table(["類型", "號碼", "層級", "規則"], rows) + "</div>"


def similarity_audit_html(analysis: dict, history: list[dict]) -> str:
    prev = [int(item["number"]) for item in (history[0].get("candidates", []) if history else [])[:9]]
    current = top_numbers(analysis, 9)
    overlap = sorted(set(prev) & set(current))
    rows = [
        ["上期前九", fmt_numbers(prev) or "-", "比對基準", "禁止直接沿用"],
        ["本期前九", fmt_numbers(current), "本期輸出", "每期重算"],
        ["重疊號", fmt_numbers(overlap) or "-", f"{len(overlap)}/9", "未達連莊守門不得沿用"],
        ["達標連莊", "重新驗算", "通過才保留", "已檢查"],
    ]
    return '<div class="band"><h2>近期預測相似度稽核</h2>' + table(["項目", "號碼", "數據", "判定"], rows) + "</div>"


def hard_iron_html(analysis: dict) -> str:
    latest = analysis.get("latest_draw") or {}
    signature = hashlib.sha256(json.dumps([latest.get("draw_date"), latest.get("numbers"), top_numbers(analysis, 15)], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    rows = [
        ["重新運算", "已重新運算", "本期依最新開獎資料重新運算、重新回測、重新檢討；禁止沿用上期預測。"],
        ["依據開獎", latest.get("draw_date", "-"), fmt_numbers(latest.get("numbers", []))],
        ["下期目標", analysis.get("target_draw_date", "-"), target_label(analysis)],
        ["資料真實性", (analysis.get("data_integrity_gate") or {}).get("status", "-"), (analysis.get("data_integrity_gate") or {}).get("rule", "-")],
        ["重算指紋", signature, "每期會因最新開獎與預測結果改變"],
    ]
    guard = analysis.get("industrial_engine", {}).get("previous_prediction_guard", {})
    rows2 = [
        ["守門狀態", "上期前十五軟降權並重新驗證", "硬性啟用", guard.get("policy", "-")],
        ["本期前九重疊", fmt_numbers(guard.get("overlap_numbers", [])) or "-", f"{guard.get('current_top9_overlap', 0)}/9", "只允許達標連莊進前九"],
        ["前九替換", "剔除未達標", "補入備查池", "每期開獎後重新運算"],
    ]
    return '<div class="band"><h2>鐵律守門</h2>' + table(["項目", "結果", "說明"], rows) + table(["項目", "號碼 / 狀態", "結果", "說明"], rows2) + "</div>"


def stability_governor_html(analysis: dict, settled: dict) -> str:
    rolling = analysis.get("rolling_error_adjustment") or {}
    failed_models = rolling.get("failed_models_reweighted") or []
    low_hit = analysis.get("low_hit_regime_shift") or {}
    memory = low_hit.get("failure_memory") or {}
    front9 = analysis.get("front9_escape_correction") or {}
    optimizer = analysis.get("hit_rate_optimizer") or {}
    gate = analysis.get("high_confidence_gate") or {}
    external = analysis.get("external_method_weight_shift") or {}
    rows = [
        ["已套用修正", "最新開獎後已重新排序、回測、同步手機獨立頁"],
        ["已套用修正", f"外部模式權重：{external.get('status', '-')}；配對Top9 {external.get('pair_lift_top9_avg', '-')}"],
        ["已套用修正", f"命中率強化：拉進 {fmt_numbers(optimizer.get('promoted_numbers', [])) or '-'}；降下 {fmt_numbers(optimizer.get('demoted_numbers', [])) or '-'}"],
        ["已套用修正", f"高機率校準：{gate.get('status', '-')}；Top9 {gate.get('top9_avg_hits', '-')} / 隨機 {gate.get('random_top9_expectation', '-')}"],
        ["已套用修正", "重複預測但未通過守門者自動降權"],
        ["已套用修正", "第10到15名補中能力獨立追蹤並壓回前九"],
        ["已套用修正", f"錯誤模組滾動重算：{', '.join(failed_models) if failed_models else '本期無硬降權'}"],
        ["已套用修正", f"低命中權重轉換：{low_hit.get('mode', '-')} / 嚴重度 {low_hit.get('severity', '-')}"],
        ["已套用修正", f"9名後外溢校正：拉回 {fmt_numbers(front9.get('promoted_numbers', [])) or '-'}；降下 {fmt_numbers(front9.get('demoted_numbers', [])) or '-'}"],
        ["已套用修正", f"漏抓回補：{fmt_numbers(memory.get('top_leak_numbers', [])[:8]) or '-'}"],
        ["已套用修正", f"落空降權：{fmt_numbers(memory.get('top_penalty_numbers', [])[:8]) or '-'}"],
    ]
    strict = analysis.get("industrial_engine", {}).get("strict_validation_gate", {})
    blocked_numbers = [item for item in (analysis.get("candidates") or []) if item.get("last_draw_repeat")]
    blocked = [[f"{int(item['number']):02d}", "最新開獎號", item.get("support_models", "-"), item.get("strict_guard", "-")] for item in blocked_numbers[:8]]
    if not blocked:
        blocked = [["-", "目前沒有硬降權號碼", strict.get("validated_count", "-"), strict.get("policy", "-")]]
    watch = []
    for item in (analysis.get("candidates") or [])[:8]:
        watch.append([f"{int(item['number']):02d}", item.get("rank", "-"), item.get("stability_count", "-"), (item.get("previous_prediction_guard") or {}).get("message", "-")])
    recent = []
    if settled:
        pack = ((settled.get("strong_packs") or {}).get("strong_single") or {}).get("numbers", [])
        recent.append([settled.get("actual_date", "-"), fmt_numbers(pack), fmt_numbers(settled.get("actual_numbers", [])), settled.get("top5_hits", "-")])
    return (
        '<div class="band warn"><h2>穩定治理與錯誤修正紀錄</h2>'
        f'<p>檢查時間：{esc(display_time(analysis.get("generated_at_taiwan", "-")))} / 狀態：每期開獎後重算、回測、同步手機。</p>'
        "<h3>本次修正動作</h3>"
        f'{table(["類別", "內容"], rows)}'
        "<h3>獨隻硬降權名單</h3>"
        f'{table(["號碼", "原狀態", "通過關數", "原因"], blocked)}'
        "<h3>獨隻近期觀察降權</h3>"
        f'{table(["號碼", "排名", "穩定", "守門"], watch)}'
        "<h3>最近獨隻結算</h3>"
        f'{table(["開獎日", "獨隻", "實際開獎", "前五命中"], recent, "目前沒有最近獨隻結算")}'
        "</div>"
    )


def reality_gate_html(analysis: dict) -> str:
    release = analysis.get("release_gate") or {}
    bt = analysis.get("backtest") or {}
    rows = [
        ["獨隻1中1", "目標95%", release.get("reason", "-"), "未過門檻只列觀察"],
        ["2中1~2", "目標95%", pct(((bt.get("pack_summary") or {}).get("two_hit_one") or {}).get("pass_rate")), "每期回測後放行"],
        ["3中1~3", "目標95%", pct(((bt.get("pack_summary") or {}).get("three_hit_one") or {}).get("pass_rate")), "需多模型交叉通過"],
        ["5中2~3", "目標95%", bt.get("top10_avg_hits", "-"), "未達標降級"],
        ["9中3~5", "目標95%", bt.get("top15_avg_hits", "-"), "只用前九核心顯示"],
        ["正式發布守門", "觀察中", bt.get("top9_edge_vs_random", "-"), "未達標不得包裝高信心"],
    ]
    return '<div class="band"><h2>實戰門檻</h2>' + table(["項目", "門檻", "目前數據", "處理"], rows) + "</div>"


def monthly_breakthrough_html(analysis: dict, history: list[dict]) -> str:
    month = previous_month_key(month_key(analysis, history))
    rows = monthly_rows(history, analysis)
    rows2 = [
        ["模式", "月度精準守門", "主層", "前九名"],
        ["相對穩定組", fmt_numbers(top_numbers(analysis, 5)), "正式高機率", "禁止保證"],
        ["動作", "強獨月度通過率未達門檻前禁止正式發布", "狀態", "已套用"],
        ["動作", "本月落空號軟降權，後段命中回收提高", "狀態", "已套用"],
    ]
    return f'<div class="band"><h2>{esc(month)} 上月總檢討與本期突破校正</h2>' + table(["項目", "數值", "判讀", "狀態"], rows) + table(["類別", "內容", "管制", "狀態"], rows2) + "</div>"


def super_single_html(analysis: dict) -> str:
    single = strong_single_numbers(analysis)
    item = strong_single_candidate(analysis)
    pack = ((analysis.get("strong_packs") or {}).get("strong_single") or {})
    audit = pack.get("selection_audit") or {}
    support = f"{item.get('support_models', '-')}/{item.get('verification_denominator', len(MODEL_LABELS))}"
    return f"""
    <div class="band singlebox">
      <h2>最強獨隻1中1</h2>
      <div class="grid">
        <div class="card hot-card"><div class="label">獨隻號碼</div><div class="value num">{esc(fmt_numbers(single) or "-")}</div></div>
        <div class="card"><div class="label">判定</div><div class="value">獨立守門獨隻</div></div>
        <div class="card"><div class="label">獨隻總分</div><div class="value">{esc(score_percent(item))}</div></div>
        <div class="card"><div class="label">模型機率</div><div class="value">{esc(probability_percent(item))}</div></div>
        <div class="card"><div class="label">交叉層數</div><div class="value">{esc(support)}</div></div>
      </div>
      <p><strong>運算邏輯：</strong>官方歷史資料庫、多模型交叉驗算、前九名核心壓縮、12/30/90期錯誤模組滾動修正。</p>
      <p><strong>來源模型：</strong>{esc("、".join((item.get("reasons") or [])[:8]))}</p>
      <p><strong>獨隻守門：</strong>{esc(audit.get("rule", "禁止直接用最新開獎號混充"))}；狀態 {esc(audit.get("status", "-"))}；候選排名 {esc(audit.get("selected_rank", "-"))}</p>
      <p><strong>風控：</strong>未過正式門檻時只列觀察，不包裝成保證。</p>
    </div>
    """


def desktop_css() -> str:
    return """
    body{margin:0;background:#f5f7fb;color:#172033;font-family:"Microsoft JhengHei",Arial,sans-serif;}
    header{background:#111827;color:white;padding:22px 24px;}
    main{max-width:1180px;margin:0 auto;padding:18px;}
    .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;position:sticky;top:0;z-index:5;background:#f5f7fb;padding:10px 0;}
    .tabs button{border:1px solid #cbd5e1;background:white;border-radius:7px;padding:10px 14px;font-weight:800;cursor:pointer;}
    .tabs button.active{background:#0f766e;color:white;border-color:#0f766e;}
    .panel{display:none;}
    .panel.active{display:block;}
    .band{background:white;border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin-bottom:14px;overflow:auto;}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;}
    .card{border:1px solid #e5e7eb;border-radius:8px;padding:12px;background:#fbfdff;}
    .hot-card{border-color:#fecaca;background:#fff1f2;}
    .singlebox{border-color:#fecaca;background:#fffafa;}
    .warn{background:#fff7ed;border-color:#fed7aa;}
    .date-ribbon{background:#ecfeff;border-color:#67e8f9;}
    .label{font-size:13px;color:#64748b;font-weight:700;}
    .value{font-size:22px;font-weight:900;margin-top:6px;}
    table{width:100%;border-collapse:collapse;min-width:760px;}
    th,td{border-bottom:1px solid #e5e7eb;padding:9px;text-align:left;vertical-align:top;}
    th{background:#f1f5f9;}
    .num{font-size:20px;font-weight:900;color:#b91c1c;}
    .small{font-size:13px;line-height:1.5;}
    .verify-table{min-width:1420px;}
    a{color:#0f766e;font-weight:800;}
    @media(max-width:680px){main{padding:10px}header{padding:16px}table{min-width:680px}}
    """


def build_desktop_html(analysis: dict, settled: dict, history: list[dict]) -> str:
    decorate_analysis(analysis)
    latest = analysis.get("latest_draw") or {}
    history_info = analysis.get("history_completeness") or {}
    latest_tw = latest_label(analysis)
    target_tw = target_label(analysis)
    latest_numbers = fmt_numbers(latest.get("numbers", []))
    target_date = analysis.get("target_draw_date", "-")
    report_time = display_time(analysis.get("generated_at_taiwan", "-"))
    history_note = history_info.get("note") or ""
    high_numbers = [item.get("number") for item in (analysis.get("latest_ironlaw") or {}).get("high_confidence_numbers", [])]
    top9 = top_numbers(analysis, 9)
    date_text = history_info.get("date_range") or history_info.get("range") or history_info.get("status") or "完整"
    full_body = standard_full_body(analysis, settled, history)
    return f"""<!doctype html>
<html lang="zh-Hant" data-compact-report="true">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>迦納彩39 精算預測戰報</title>
  <style>{desktop_css()}</style>
</head>
<body>
<header>
  <h1>迦納彩39 精算預測戰報</h1>
  <p>非洲迦納彩 Daywa 5/39 Direct / 產生時間 {esc(report_time)} / 全歷史資料 {esc(compact_status(history_info.get('status', '完整')))} / 共 {esc(analysis.get('draw_count', '-'))} 筆</p>
  <p class="small">{esc(history_note)}</p>
  <p>台灣最新可確認時間 {esc(latest_tw)} / {esc(latest_numbers)}　下期預測台灣時間 {esc(target_tw)}</p>
  <p class="small">對應開獎日 {esc(latest.get('draw_date', '-'))} / 下期對應開獎日 {esc(target_date)}</p>
</header>
<main>{full_body}</main>
</body>
</html>"""
    return f"""<!doctype html>
<html lang="zh-Hant" data-compact-report="true">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>迦納彩39 精算預測戰報</title>
  <style>{desktop_css()}</style>
</head>
<body>
<header>
  <h1>迦納彩39 精算預測戰報</h1>
  <p>非洲迦納彩 Daywa 5/39 Direct / 產生時間 {esc(report_time)} / 全歷史資料 {esc(compact_status(history_info.get('status', '完整')))} / 共 {esc(analysis.get('draw_count', '-'))} 筆</p>
  <p class="small">{esc(history_note)}</p>
  <p>台灣最新可確認時間 {esc(latest_tw)} / {esc(latest_numbers)}　下期預測台灣時間 {esc(target_tw)}</p>
  <p class="small">對應開獎日 {esc(latest.get('draw_date', '-'))} / 下期對應開獎日 {esc(target_date)}</p>
</header>
<main>
  <nav class="tabs">
    {''.join(f'<button class="{"active" if idx == 0 else ""}" data-tab="{key}">{label}</button>' for idx, (key, label) in enumerate(DESKTOP_TABS))}
  </nav>
  <section class="band date-ribbon">
    <h2>本報表日期對照</h2>
    <div class="grid">
      <div class="card"><div class="label">全歷史資料範圍</div><div class="value">{esc(compact_status(date_text))}</div></div>
      <div class="card"><div class="label">資料依據台灣可確認時間</div><div class="value">{esc(latest_tw)}</div></div>
      <div class="card"><div class="label">最新開獎號碼</div><div class="value">{esc(latest_numbers)}</div></div>
      <div class="card"><div class="label">資料對應開獎日</div><div class="value">{esc(latest.get('draw_date', '-'))}</div></div>
      <div class="card"><div class="label">下期預測台灣時間</div><div class="value">{esc(target_tw)}</div></div>
      <div class="card"><div class="label">下期對應開獎日</div><div class="value">{esc(target_date)}</div></div>
      <div class="card"><div class="label">戰報產生時間</div><div class="value">{esc(report_time)}</div></div>
    </div>
  </section>
  <section id="prediction" class="panel active">
    <div class="band">
      <h2>核心決策（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2>
      <div class="grid">
        <div class="card"><div class="label">資料狀態</div><div class="value">{esc(compact_status((analysis.get('freshness') or {}).get('status')))}</div></div>
        <div class="card"><div class="label">檢查</div><div class="value">已重算</div></div>
        <div class="card"><div class="label">下期預測台灣時間</div><div class="value">{esc(target_tw)}</div></div>
        <div class="card hot-card"><div class="label">獨隻</div><div class="value">{esc(fmt_numbers(strong_single_numbers(analysis)) or "-")}</div></div>
        <div class="card"><div class="label">九碼核心</div><div class="value">{esc(fmt_numbers(top9) or "-")}</div></div>
      </div>
      <p>運算原則：只顯示完成運算後的精準資訊；依官方歷史資料庫、多模型交叉驗算與滾動回測輸出。</p>
      <p><strong>高機率信心牌：</strong>{esc(fmt_numbers(high_numbers) or "本期未過正式高信心守門")}</p>
    </div>
    {super_single_html(analysis)}
    <div class="band">
      <h2>下期研究候選前9名（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2>
      {table(["號碼", "資料依據台灣時間", "預測台灣時間", "排名", "分數", "信心", "機率", "遺漏", "驗算數", "驗算來源"], [[row[0], latest_tw, target_tw] + row[1:] for row in candidate_rows(analysis, 9)])}
    </div>
    <div class="band warn">
      <h2>第10到第15名第二層備查（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2>
      <p>第10到第15名獨立列出為第二層備查池，不直接混入前九高信心核心；若連續達標，滾動模型會自動拉升權重。</p>
      {table(["排名", "號碼", "分數", "信心", "機率", "交叉驗算", "穩定與遺漏", "成熟度", "定位"], backup_rank_rows(analysis), "本期沒有第10到第15名備查資料")}
      <h3>最近第10到15名補中統計</h3>
      {table(["項目", "數值", "比例或合計", "說明"], backup_summary_rows(history), "目前沒有已結算的第10到15名統計")}
      {table(["開獎日", "第10到15名", "補中號", "補中顆數", "前九命中顆數", "判讀"], backup_hit_rows(history), "目前沒有第10到15名補中明細")}
    </div>
    <div class="band">
      <h2>生成號碼逐號驗算（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2>
      <p>每一個推薦號碼都必須列出版路、拖牌或共現檢查、交叉驗算、上期沿用守門與成熟度；未通過守門不得進入下期前九。</p>
      {table(["號碼", "資料依據台灣時間", "預測台灣時間", "排名", "版路分類", "來源證據", "交叉驗算", "穩定與遺漏", "守門驗證", "結論"], verification_rows(analysis, 9), table_class="verify-table")}
    </div>
    <div class="band">
      <h2>強牌組精算（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2>
      {table(["類型", "號碼", "狀態", "回測期", "達標率", "平均命中", "判定"], pack_rows(analysis))}
    </div>
  </section>
  <section id="review" class="panel">
    {hits_html(settled, history)}
    <div class="band"><h2>上期命中檢討（{esc(settled.get('based_on_date', '-') if settled else '-')} 預測 / {esc(settled.get('actual_date', '-') if settled else '-')} 開獎）</h2>{table(["牌組", "預測號", "命中", "命中號", "結果"], pack_review_rows(settled), "目前沒有強牌結算")}</div>
    {prediction_rebuild_html(analysis, settled)}
  </section>
  <section id="monthly" class="panel">{monthly_html(analysis, history)}</section>
  <section id="avoid" class="panel">
    <div class="band"><h2>低機率達標檢討（{esc(settled.get('based_on_date', '-') if settled else '-')} 預測 / {esc(settled.get('actual_date', '-') if settled else '-')} 開獎）</h2>{table(["目標日", "暫避包", "預測號", "開獎日", "實際開獎", "誤中", "誤中號", "結果"], low_daily_rows(history[:1]), "目前沒有低機率上期檢討")}</div>
    <div class="band warn"><h2>低機率（資料依據台灣時間 {esc(latest_tw)} / 預測台灣時間 {esc(target_tw)}）</h2><p>低機率分析獨立顯示 5不中、10不中、15不中 摘要；低機率不等於絕對不開。</p>{table(["暫避包", "號碼", "信心指標", "平均暫避分", "明細"], low_summary_rows(analysis))}</div>
    <div class="band"><h2>低機率每日紀錄</h2>{table(["目標日", "暫避包", "預測號", "開獎日", "實際開獎", "誤中", "誤中號", "結果"], low_daily_rows(history), "目前沒有低機率每日紀錄")}</div>
    <div class="band"><h2>低機率每月總紀錄分析</h2>{table(["暫避包", "結算期數", "達標期數", "達標率", "平均誤中", "最差日期", "最常誤中"], low_monthly_rows(history), "目前沒有低機率每月結算資料")}</div>
  </section>
  <section id="models" class="panel">
    {formula_lab_html(analysis)}
    {prediction_rebuild_html(analysis, settled)}
    {dual_track_html(analysis, history)}
    <div class="band"><h2>模型成效（資料截至台灣時間 {esc(latest_tw)} / 回測產生 {esc(report_time)}）</h2>{table(["模型", "回測期", "前五平均", "前十平均", "前十五平均", "前十優勢"], model_rows(analysis))}</div>
    <div class="band"><h2>強牌實戰統計</h2>{table(["類型", "號碼", "狀態", "回測期", "達標率", "平均命中", "判定"], pack_rows(analysis))}</div>
    <div class="band"><h2>模型滾動調整</h2>{table(["模型", "動作", "近期優勢", "長期優勢", "原因"], lifecycle_rows(analysis, history))}</div>
  </section>
  <section id="system" class="panel">
    {similarity_audit_html(analysis, history)}
    {hard_iron_html(analysis)}
    {stability_governor_html(analysis, settled)}
    {reality_gate_html(analysis)}
    {monthly_breakthrough_html(analysis, history)}
  </section>
</main>
<script>
  document.querySelectorAll('.tabs button').forEach(btn=>btn.addEventListener('click',()=>{{
    document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
  }}));
</script>
</body>
</html>"""


def build_markdown(analysis: dict, settled: dict, history: list[dict]) -> str:
    decorate_analysis(analysis)
    latest = analysis.get("latest_draw") or {}
    lines = [
        "# 迦納彩39 精算預測戰報 - 非洲迦納彩 Daywa 5/39 Direct",
        "",
        f"- 產生時間：{display_time(analysis.get('generated_at_taiwan', '-'))}",
        f"- 台灣最新可確認時間：{latest_label(analysis)} / {fmt_numbers(latest.get('numbers', []))}",
        f"- 下期預測台灣時間：{target_label(analysis)}",
        f"- 全歷史資料：{compact_status((analysis.get('history_completeness') or {}).get('status', '-'))} / 共 {analysis.get('draw_count', '-')} 筆",
        f"- 歷史缺口：{(analysis.get('history_completeness') or {}).get('note', '-')}",
        "",
        "## 核心決策",
        f"- 資料狀態：{compact_status((analysis.get('freshness') or {}).get('status'))}",
        "- 檢查：已重算",
        f"- 獨隻：{fmt_numbers(strong_single_numbers(analysis))}",
        f"- 九碼核心：{fmt_numbers(top_numbers(analysis, 9))}",
        f"- 高機率信心牌：{fmt_numbers([item.get('number') for item in (analysis.get('latest_ironlaw') or {}).get('high_confidence_numbers', [])]) or '本期未過正式高信心守門'}",
        "",
        "## 下期研究候選前9名",
        "| 號碼 | 資料依據台灣時間 | 預測台灣時間 | 排名 | 分數 | 信心 | 機率 | 遺漏 | 驗算數 | 驗算來源 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    latest_tw = latest_label(analysis)
    target_tw = target_label(analysis)
    for row in candidate_rows(analysis, 9):
        lines.append("| " + " | ".join(str(cell) for cell in ([row[0], latest_tw, target_tw] + row[1:])) + " |")
    lines.extend(["", "## 強牌組精算", "| 類型 | 號碼 | 狀態 | 回測期 | 達標率 | 平均命中 | 判定 |", "| --- | --- | --- | --- | ---: | ---: | --- |"])
    for row in pack_rows(analysis):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(["", "## 命中檢討", "| 項目 | 結果 |", "| --- | --- |"])
    for row in review_latest_rows(settled):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.extend(["", "## 低機率", "| 暫避包 | 號碼 | 信心指標 | 平均暫避分 | 明細 |", "| --- | --- | --- | ---: | --- |"])
    for row in low_summary_rows(analysis):
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    lines.append("\n## 風險聲明\n" + str(analysis.get("risk_notice", "樂透為隨機遊戲，不保證命中或獲利。")))
    return "\n".join(lines) + "\n"


def mobile_css() -> str:
    return """
    :root{color-scheme:light;--ink:#172033;--muted:#64748b;--line:#dbe4ef;--brand:#0f766e;--hot:#b91c1c;--soft:#f6f8fb;}
    *{box-sizing:border-box}
    body{margin:0;background:var(--soft);color:var(--ink);font-family:"Microsoft JhengHei",Arial,sans-serif;padding-bottom:86px}
    header{padding:18px 16px 14px;background:#111827;color:white;position:sticky;top:0;z-index:10;box-shadow:0 8px 20px rgba(15,23,42,.16)}
    h1{margin:0 0 6px;font-size:22px;letter-spacing:0}
    header p{margin:4px 0;color:#dbeafe;line-height:1.45;font-size:13px}
    main{padding:12px;max-width:720px;margin:0 auto}
    .band,.card,.number-card,.pack-card{background:white;border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:10px;overflow:auto}
    .launch-panel{border:3px solid #166534;background:#f0fdf4}
    .warn{background:#fff7ed;border-color:#fed7aa}
    .grid{display:grid;gap:10px}
    .label{color:var(--muted);font-size:13px;font-weight:800}
    .value{margin-top:5px;font-size:21px;font-weight:900;line-height:1.25;color:#0f172a;overflow-wrap:anywhere}
    .num{font-size:20px;font-weight:900;color:#b91c1c}
    h2{margin:0 0 10px;font-size:18px}
    h3{margin:12px 0 8px;font-size:16px}
    .launch-title{margin:0 0 10px;font-size:18px;font-weight:900}
    p{line-height:1.5}
    .mobile-action,.mobile-refresh{display:block;width:100%;box-sizing:border-box;text-align:center;padding:14px;border:0;border-radius:8px;font-weight:900;text-decoration:none}
    .mobile-action{background:#166534;color:#fff}
    .mobile-refresh{margin-top:10px;background:#1d4ed8;color:#fff;font-size:16px}
    .cloud-update-link{display:block;margin-top:12px;text-align:center;color:#1d4ed8;font-weight:900}
    .cloud-note{font-weight:800;color:#14532d;word-break:break-all}
    .quick-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}
    .quick-links a{display:block;text-align:center;text-decoration:none;background:#0f766e;color:white;font-weight:900;border-radius:8px;padding:12px 8px}
    .quick-links a.secondary{background:#334155}
    table{width:100%;border-collapse:collapse;min-width:0;font-size:13px}
    th,td{border-bottom:1px solid #e5eaf2;padding:8px;text-align:left;vertical-align:top}
    th{background:#f1f5f9;color:#334155}
    .bottom-nav{position:fixed;left:0;right:0;bottom:0;z-index:12;display:grid;grid-template-columns:repeat(5,1fr);background:white;border-top:1px solid var(--line);padding:7px 6px calc(7px + env(safe-area-inset-bottom));gap:6px}
    .bottom-nav a{text-decoration:none;color:#334155;text-align:center;font-size:12px;font-weight:900;padding:8px 4px;border-radius:7px;background:#f8fafc}
    .bottom-nav a.active{background:#0f766e;color:white}
    @media(max-width:720px){
      main{padding:10px}
      .band,.card,.number-card,.pack-card{padding:11px;margin-bottom:9px}
      .value{font-size:18px}
      table{min-width:0;font-size:14px;border-collapse:separate;border-spacing:0}
      table thead{display:none}
      table tbody,table tr,table td{display:block;width:100%}
      table tr{border:1px solid #e5eaf2;border-radius:8px;margin:8px 0;background:#fff;overflow:hidden}
      table td{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #eef2f7;padding:9px 10px;text-align:right;word-break:break-word}
      table td:last-child{border-bottom:0}
      table td::before{content:attr(data-label);font-weight:900;color:#64748b;text-align:left;flex:0 0 42%;max-width:42%}
      table td[colspan]::before{content:""}
      .quick-links{grid-template-columns:repeat(2,minmax(0,1fr))}
    }
    """


def mobile_script(version: str, home: str = "home.html") -> str:
    return f"""
    <script>
    window.GHANA39_BUILD_VERSION = '{esc(version)}';
    window.GHANA39_HOME_PAGE = '{esc(home)}';
    function setMobileStatus(text) {{
      var el = document.getElementById('mobileUpdateStatus');
      if (el) el.textContent = text;
    }}
    function normalizeMobilePage(page) {{
      if (!page || page === 'home' || page === 'index') return 'full-report.html';
      if (page.indexOf('.') === -1) return page + '.html';
      if (page === 'home.html' || page === 'index.html') return 'full-report.html';
      return page;
    }}
    function currentMobilePage() {{
      var path = window.location.pathname || '';
      var page = path.split('/').pop() || '';
      return normalizeMobilePage(page);
    }}
    function normalizeCurrentMobileUrl() {{
      var path = window.location.pathname || '';
      var page = path.split('/').pop() || '';
      var fixed = normalizeMobilePage(page);
      if (page !== fixed && window.history && window.history.replaceState) {{
        var base = path.slice(0, Math.max(0, path.length - page.length));
        window.history.replaceState(null, document.title, base + fixed + window.location.search + window.location.hash);
      }}
    }}
    normalizeCurrentMobileUrl();
    async function clearMobileCaches() {{
      if ('serviceWorker' in navigator) {{
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map(async function(reg) {{
          try {{
            if (reg.active) reg.active.postMessage({{ type: 'CLEAR_CACHE' }});
            await reg.update();
            await reg.unregister();
          }} catch (err) {{}}
        }}));
      }}
      if ('caches' in window) {{
        const keys = await caches.keys();
        await Promise.all(keys.map(function(key) {{ return caches.delete(key); }}));
      }}
    }}
    async function forceRefresh() {{
      setMobileStatus('更新中 ' + new Date().toLocaleTimeString());
      await clearMobileCaches();
      location.replace(currentMobilePage() + '?v={esc(version)}&force=' + Date.now());
    }}
    async function autoRefreshIfStale() {{
      try {{
        const res = await fetch('version.json?check=' + Date.now(), {{ cache: 'no-store' }});
        if (!res.ok) return;
        const data = await res.json();
        const stamp = String(data.version || data.generated_at_taiwan || '').replace(/\\D/g, '').slice(0, 14);
        if (stamp && stamp !== window.GHANA39_BUILD_VERSION && !sessionStorage.getItem('ghana39_refreshed_' + stamp)) {{
          sessionStorage.setItem('ghana39_refreshed_' + stamp, '1');
          await clearMobileCaches();
          location.replace(currentMobilePage() + '?v=' + stamp + '&auto=' + Date.now());
        }}
      }} catch (err) {{}}
    }}
    if ('serviceWorker' in navigator) {{
      window.addEventListener('load', function(){{
        navigator.serviceWorker.register('service-worker.js?v={esc(version)}', {{ updateViaCache: 'none' }}).then(function(reg){{ reg.update(); }}).catch(function(){{}});
        autoRefreshIfStale();
        setInterval(autoRefreshIfStale, 30000);
      }});
      document.addEventListener('visibilitychange', function() {{ if (!document.hidden) autoRefreshIfStale(); }});
      window.addEventListener('online', autoRefreshIfStale);
    }} else {{
      window.addEventListener('load', autoRefreshIfStale);
      setInterval(autoRefreshIfStale, 30000);
    }}
    </script>
    """


def mobile_nav(active: str) -> str:
    items = [
        ("home", "首頁", "home.html"),
        ("prediction", "預測", "prediction.html"),
        ("review", "檢討", "review.html"),
        ("avoid", "低機率", "low-probability.html"),
        ("full", "完整", "full-report.html"),
    ]
    return '<nav class="bottom-nav">' + "".join(f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>' for key, label, href in items) + "</nav>"


def mobile_shell(title: str, active: str, analysis: dict, body: str) -> str:
    version = build_version(analysis)
    latest = analysis.get("latest_draw") or {}
    history_info = analysis.get("history_completeness") or {}
    history_status = compact_status(history_info.get("status", "-"))
    history_note = history_info.get("note") or ""
    return f"""<!doctype html>
<html lang="zh-Hant" data-mobile-independent="true">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <meta name="theme-color" content="#111827">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Ghana39">
  <link rel="manifest" href="manifest.webmanifest?v={esc(version)}">
  <title>{esc(title)}</title>
  <style>{mobile_css()}</style>
</head>
<body>
<header>
  <h1>{esc(title)}</h1>
  <p>非洲迦納彩 Daywa 5/39 Direct / 版本 {esc(version)}</p>
  <p>最新 {esc(latest.get('draw_date', '-'))}：{esc(fmt_numbers(latest.get('numbers', [])))}；預測 {esc(analysis.get('target_draw_date', '-'))}</p>
  <p>歷史資料：{esc(history_status)} / 共 {esc(analysis.get('draw_count', '-'))} 筆</p>
</header>
<main>
  <section class="band launch-panel">
    <div class="launch-title">迦納彩39 手機雲端獨立版</div>
    <button class="mobile-refresh" type="button" onclick="forceRefresh()">重新讀取雲端最新頁</button>
    <a class="cloud-update-link" href="clear-cache.html?v={esc(version)}">手機沒更新點這裡清除舊快取</a>
    <div class="quick-links">
      <a href="prediction.html?v={esc(version)}">下期預測</a>
      <a href="review.html?v={esc(version)}">上期檢討</a>
      <a href="low-probability.html?v={esc(version)}" class="secondary">低機率</a>
      <a href="full-report.html?v={esc(version)}" class="secondary">完整戰報</a>
    </div>
    <p class="cloud-note">本頁為獨立雲端手機版：首頁、下期預測、上期檢討、低機率、完整戰報都在手機站內完成。</p>
    <p class="cloud-note">{esc(history_note)}</p>
    <p class="cloud-note" id="mobileUpdateStatus">版本 {esc(version)}</p>
  </section>
  {body}
</main>
{mobile_nav(active)}
{mobile_script(version)}
</body>
</html>"""


def mobile_prediction_body(analysis: dict, history: list[dict]) -> str:
    latest_tw = latest_label(analysis)
    target_tw = target_label(analysis)
    high = [item.get("number") for item in (analysis.get("latest_ironlaw") or {}).get("high_confidence_numbers", [])]
    return f"""
    <section class="band">
      <h2>核心決策</h2>
      <div class="grid">
        <div class="card hot-card"><div class="label">獨隻</div><div class="value">{esc(fmt_numbers(strong_single_numbers(analysis)))}</div></div>
        <div class="card"><div class="label">九碼核心</div><div class="value">{esc(fmt_numbers(top_numbers(analysis, 9)))}</div></div>
        <div class="card"><div class="label">資料依據台灣時間</div><div class="value">{esc(latest_tw)}</div></div>
        <div class="card"><div class="label">預測台灣時間</div><div class="value">{esc(target_tw)}</div></div>
      </div>
      <p><strong>高機率信心牌：</strong>{esc(fmt_numbers(high) or "本期未過正式高信心守門")}</p>
    </section>
    {super_single_html(analysis)}
    <section class="band"><h2>下期研究候選前9名</h2>{table(["號碼", "資料依據台灣時間", "預測台灣時間", "排名", "分數", "信心", "機率", "遺漏", "驗算數", "驗算來源"], [[row[0], latest_tw, target_tw] + row[1:] for row in candidate_rows(analysis, 9)])}</section>
    <section class="band warn"><h2>第10到第15名第二層備查</h2>{table(["排名", "號碼", "分數", "信心", "機率", "交叉驗算", "穩定與遺漏", "成熟度", "定位"], backup_rank_rows(analysis))}</section>
    <section class="band"><h2>生成號碼逐號驗算</h2>{table(["號碼", "資料依據台灣時間", "預測台灣時間", "排名", "版路分類", "來源證據", "交叉驗算", "穩定與遺漏", "守門驗證", "結論"], verification_rows(analysis, 9))}</section>
    <section class="band"><h2>強牌組精算</h2>{table(["類型", "號碼", "狀態", "回測期", "達標率", "平均命中", "判定"], pack_rows(analysis))}</section>
    """


def build_mobile_pages(analysis: dict, settled: dict, history: list[dict]) -> dict[str, str]:
    decorate_analysis(analysis)
    prediction_body = mobile_prediction_body(analysis, history)
    review_body = hits_html(settled, history) + '<section class="band">' + table(["牌組", "預測號", "命中", "命中號", "結果"], pack_review_rows(settled), "目前沒有強牌結算") + "</section>"
    avoid_body = (
        '<section class="band warn"><h2>下期低機率暫避預測</h2>'
        f'<p><strong>台灣開獎時間：</strong>{esc(target_label(analysis))} / <strong>對應開獎日：</strong>{esc(analysis.get("target_draw_date", "-"))}</p>'
        f'{table(["暫避包", "號碼", "信心指標", "平均暫避分", "明細"], low_summary_rows(analysis))}</section>'
        '<section class="band"><h2>低機率每日紀錄</h2>'
        f'{table(["目標日", "暫避包", "預測號", "開獎日", "實際開獎", "誤中", "誤中號", "結果"], low_daily_rows(history), "目前沒有低機率每日紀錄")}</section>'
        '<section class="band"><h2>低機率每月總紀錄分析</h2>'
        f'{table(["暫避包", "結算期數", "達標期數", "達標率", "平均誤中", "最差日期", "最常誤中"], low_monthly_rows(history), "目前沒有低機率每月結算資料")}</section>'
    )
    monthly_body = monthly_html(analysis, history)
    models_body = formula_lab_html(analysis) + prediction_rebuild_html(analysis, settled) + dual_track_html(analysis, history) + '<section class="band">' + table(["模型", "回測期", "前五平均", "前十平均", "前十五平均", "前十優勢"], model_rows(analysis)) + "</section>"
    system_body = similarity_audit_html(analysis, history) + hard_iron_html(analysis) + stability_governor_html(analysis, settled) + reality_gate_html(analysis) + monthly_breakthrough_html(analysis, history)
    full_body = standard_full_body(analysis, settled, history)
    pages = {
        "index.html": mobile_shell("迦納彩39 完整戰報", "full", analysis, full_body),
        "home.html": mobile_shell("迦納彩39 完整戰報", "full", analysis, full_body),
        "首頁.html": mobile_shell("迦納彩39 完整戰報", "full", analysis, full_body),
        "prediction.html": mobile_shell("迦納彩39 下期預測", "prediction", analysis, prediction_body),
        "下期預測.html": mobile_shell("迦納彩39 下期預測", "prediction", analysis, prediction_body),
        "review.html": mobile_shell("迦納彩39 上期檢討", "review", analysis, review_body),
        "上期未命中檢討.html": mobile_shell("迦納彩39 上期檢討", "review", analysis, review_body),
        "full-report.html": mobile_shell("迦納彩39 完整戰報", "full", analysis, full_body),
        "完整戰報.html": mobile_shell("迦納彩39 完整戰報", "full", analysis, full_body),
        "latest_battle_report.html": mobile_shell("迦納彩39 完整戰報", "full", analysis, full_body),
        "low-probability.html": mobile_shell("迦納彩39 低機率精準暫避", "avoid", analysis, avoid_body),
        "低機率精準暫避.html": mobile_shell("迦納彩39 低機率精準暫避", "avoid", analysis, avoid_body),
        "monthly.html": mobile_shell("迦納彩39 每月總整理", "home", analysis, monthly_body),
        "每月總整理.html": mobile_shell("迦納彩39 每月總整理", "home", analysis, monthly_body),
        "models.html": mobile_shell("迦納彩39 模型回測", "full", analysis, models_body),
        "模型回測.html": mobile_shell("迦納彩39 模型回測", "full", analysis, models_body),
        "system.html": mobile_shell("迦納彩39 其他稽核", "full", analysis, system_body),
        "其他稽核.html": mobile_shell("迦納彩39 其他稽核", "full", analysis, system_body),
        "clear-cache.html": build_clear_cache_html(analysis),
        "清除快取.html": build_clear_cache_html(analysis),
        "manifest.webmanifest": build_manifest(),
        "service-worker.js": build_service_worker(build_version(analysis)),
        "version.json": json.dumps(version_payload(analysis), ensure_ascii=False, indent=2),
    }
    return pages


def build_clear_cache_html(analysis: dict) -> str:
    version = build_version(analysis)
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>清除快取</title><style>{mobile_css()}</style></head><body><main><section class="band launch-panel"><h1>清除手機舊快取</h1><p>按下後會清除舊版雲端頁，重新回到最新首頁。</p><button class="mobile-refresh" onclick="forceRefresh()">清除並重新載入</button><p id="mobileUpdateStatus">版本 {esc(version)}</p></section></main>{mobile_script(version)}</body></html>"""


def build_manifest() -> str:
    return json.dumps(
        {
            "name": "迦納彩39雲端手機版",
            "short_name": "迦納39",
            "start_url": "home.html",
            "display": "standalone",
            "background_color": "#f5f7fb",
            "theme_color": "#111827",
        },
        ensure_ascii=False,
        indent=2,
    )


def build_service_worker(version: str) -> str:
    return f"""
const CACHE_NAME = 'ghana39-mobile-{version}';
self.addEventListener('install', event => {{
  self.skipWaiting();
}});
self.addEventListener('activate', event => {{
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.map(key => caches.delete(key)))).then(() => self.clients.claim()));
}});
self.addEventListener('message', event => {{
  if (event.data && event.data.type === 'CLEAR_CACHE') {{
    event.waitUntil(caches.keys().then(keys => Promise.all(keys.map(key => caches.delete(key)))));
  }}
}});
self.addEventListener('fetch', event => {{
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request, {{ cache: 'no-store' }}).catch(() => caches.match(event.request)));
}});
"""


def version_payload(analysis: dict) -> dict:
    latest = analysis.get("latest_draw") or {}
    return {
        "version": build_version(analysis),
        "generated_at_taiwan": analysis.get("generated_at_taiwan"),
        "latest_draw_date": latest.get("draw_date"),
        "latest_numbers": latest.get("numbers"),
        "target_draw_date": analysis.get("target_draw_date"),
        "independent_mobile": True,
        "standard": "ghana39",
    }


def report_split_pages(analysis: dict, settled: dict, history: list[dict]) -> dict[str, str]:
    full = build_desktop_html(analysis, settled, history)
    return {
        "dashboard.html": full,
        "prediction.html": full,
        "review.html": full,
        "monthly_summary.html": full,
        "ghana39_low_probability_avoid.html": full,
        "ghana39_prediction_history.html": full,
    }


def build_outputs(analysis: dict, settled: dict, history: list[dict]) -> dict:
    decorate_analysis(analysis)
    return {
        "markdown": build_markdown(analysis, settled, history),
        "desktop_html": build_desktop_html(analysis, settled, history),
        "mobile_pages": build_mobile_pages(analysis, settled, history),
        "report_pages": report_split_pages(analysis, settled, history),
        "version": version_payload(analysis),
    }
