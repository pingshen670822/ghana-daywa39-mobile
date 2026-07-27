#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Poll official Ghana history after draw time, then rebuild desktop/mobile reports."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import california_gana39_system as system
import update_ghana_history as history


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


ROOT = Path(__file__).resolve().parent
TAIWAN_TZ = ZoneInfo("Asia/Taipei")
SYNC_STATUS = ROOT / "data" / "sync_status.json"
CLOUD_SITE_DIR = ROOT / "cloud_mobile_site"
CLOUD_PUBLIC_DIR = CLOUD_SITE_DIR / "public"
LIVE_BASE = "https://pingshen670822.github.io/ghana-daywa39-mobile"


def read_latest_analysis_date() -> str | None:
    if not system.LATEST_JSON.exists():
        return None
    try:
        data = json.loads(system.LATEST_JSON.read_text(encoding="utf-8"))
        return (data.get("latest_draw") or {}).get("draw_date")
    except Exception:
        return None


def read_fetch_summary_date() -> str | None:
    if not history.SUMMARY_JSON.exists():
        return None
    try:
        data = json.loads(history.SUMMARY_JSON.read_text(encoding="utf-8"))
        return data.get("latest_draw_date")
    except Exception:
        return None


def update_history(end_date: str | None = None) -> int:
    args = ["--sleep", "0.05"]
    if end_date:
        args.extend(["--end", end_date])
    return history.main(args)


def rebuild(rounds: int) -> dict:
    return system.run(system.DEFAULT_CSV, rounds=rounds, import_only=False)


def sync_cloud_source() -> None:
    if not CLOUD_SITE_DIR.exists():
        return
    CLOUD_PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    source_names = {source.name for source in system.SITE_DIR.iterdir()}
    for target in CLOUD_PUBLIC_DIR.iterdir():
        keep_asset = target.suffix.lower() in {".svg", ".ico", ".png", ".jpg", ".jpeg", ".webp"}
        if target.name not in source_names and not keep_asset:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
    for source in system.SITE_DIR.iterdir():
        target = CLOUD_PUBLIC_DIR / source.name
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    loader = f"""
<script id="ghana-live-sync">
(function () {{
  if (window.__ghanaLiveSyncDone) return;
  window.__ghanaLiveSyncDone = true;
  var liveBase = "{LIVE_BASE}";
  var path = window.location.pathname || "/full-report.html";
  if (path === "/" || path === "") path = "/full-report.html";
  var liveUrl = liveBase + path + "?v=" + Date.now();
  fetch(liveUrl, {{ cache: "no-store", mode: "cors" }})
    .then(function (response) {{
      if (!response.ok) throw new Error("live fetch failed");
      return response.text();
    }})
    .then(function (html) {{
      if (html.indexOf("<html") === -1 || html.indexOf("Daywa") === -1) return;
      if (document.documentElement.dataset.liveSynced === "true") return;
      document.documentElement.dataset.liveSynced = "true";
      document.open();
      document.write(html);
      document.close();
    }})
    .catch(function () {{}});
}})();
</script>
"""
    for page in CLOUD_PUBLIC_DIR.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        html = re.sub(r'(?s)<script id="ghana-live-sync">.*?</script>\s*', "", html)
        if "</body>" in html:
            html = html.replace("</body>", f"{loader}\n</body>")
        else:
            html = f"{html}\n{loader}\n"
        page.write_text(html, encoding="utf-8")


def write_status(payload: dict) -> None:
    SYNC_STATUS.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at_taiwan"] = datetime.now(TAIWAN_TZ).isoformat(timespec="seconds")
    SYNC_STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def should_accept(previous: str | None, latest: str | None, min_date: str | None, force: bool) -> bool:
    if not latest:
        return False
    if force:
        return True
    if min_date and latest >= min_date:
        return True
    if previous and latest > previous:
        return True
    return previous is None


def parse_args(argv: list[str]) -> argparse.Namespace:
    today = datetime.now(TAIWAN_TZ).date().isoformat()
    parser = argparse.ArgumentParser(description="Sync Ghana Daywa 5/39 desktop and mobile reports after draw")
    parser.add_argument("--min-date", default=today, help="Minimum latest draw date to accept, YYYY-MM-DD")
    parser.add_argument("--timeout-minutes", type=int, default=180, help="How long to poll before giving up")
    parser.add_argument("--interval-seconds", type=int, default=300, help="Polling interval")
    parser.add_argument("--rounds", type=int, default=120, help="Rolling backtest rounds")
    parser.add_argument("--force", action="store_true", help="Rebuild even if official latest date did not advance")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    previous = read_latest_analysis_date()
    deadline = datetime.now(TAIWAN_TZ) + timedelta(minutes=max(1, args.timeout_minutes))
    attempt = 0
    accepted = False
    latest = None
    while True:
        attempt += 1
        end = (datetime.now(TAIWAN_TZ).date() + timedelta(days=1)).isoformat()
        update_code = update_history(end)
        latest = read_fetch_summary_date()
        accepted = update_code == 0 and should_accept(previous, latest, args.min_date, args.force)
        write_status(
            {
                "status": "accepted" if accepted else "waiting",
                "attempt": attempt,
                "previous_latest_draw_date": previous,
                "official_latest_draw_date": latest,
                "minimum_required_date": args.min_date,
                "update_exit_code": update_code,
            }
        )
        if accepted:
            analysis = rebuild(args.rounds)
            sync_cloud_source()
            write_status(
                {
                    "status": "synced",
                    "attempt": attempt,
                    "previous_latest_draw_date": previous,
                    "official_latest_draw_date": latest,
                    "analysis_latest_draw_date": analysis["latest_draw"]["draw_date"],
                    "top9": [item["number"] for item in analysis["candidates"][:9]],
                    "desktop_report": str(system.BATTLE_HTML),
                    "mobile_report": str(system.MOBILE_INDEX),
                    "cloud_source_synced": CLOUD_SITE_DIR.exists(),
                }
            )
            print("synced", latest, system.BATTLE_HTML, system.MOBILE_INDEX)
            return 0
        if args.once or datetime.now(TAIWAN_TZ) >= deadline:
            write_status(
                {
                    "status": "timeout" if not args.once else "not_ready",
                    "attempt": attempt,
                    "previous_latest_draw_date": previous,
                    "official_latest_draw_date": latest,
                    "minimum_required_date": args.min_date,
                }
            )
            print("not ready", latest)
            return 2
        time.sleep(max(30, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
