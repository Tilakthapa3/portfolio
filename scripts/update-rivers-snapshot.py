#!/usr/bin/env python3
"""Fetch the latest DHM river readings and write data/rivers.json.

Standard library only (no pip installs on the runner). This mirrors
nepal-river-watch/fetch_rivers.py parsing and watch.py's staleness rule
(readings older than 90 minutes are stale): keep the two in sync.

Run by .github/workflows/update-rivers.yml every 30 minutes, and by
hand with:  python scripts/update-rivers-snapshot.py data/rivers.json

Exits non-zero if the DHM request fails, so the workflow never commits
an empty snapshot over a good one.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

API_URL = "https://dhm.gov.np/site/riverWatchTableViewData"
STATION_PAGE = "https://dhm.gov.np/hydrology/hms-Single/{}"
TIMEOUT = 30
STALE_MINUTES = 90
NPT = timezone(timedelta(hours=5, minutes=45), "NPT")


def to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def to_npt(iso_string):
    if not iso_string:
        return "unknown"
    try:
        return datetime.fromisoformat(iso_string).astimezone(NPT).strftime(
            "%Y-%m-%d %H:%M NPT")
    except ValueError:
        return iso_string


def parse_station(raw):
    level_field = raw.get("waterLevel")
    if isinstance(level_field, dict):
        water_level = to_float(level_field.get("value"))
        measured_at = (level_field.get("datetime") or "").strip()
    else:
        water_level = None
        measured_at = ""
    if water_level is None:
        return None
    return {
        "station_id": raw.get("id"),
        "name": (raw.get("name") or "").strip(),
        "basin": (raw.get("basin") or "").strip() or None,
        "district": (raw.get("district") or "").strip() or None,
        "water_level": water_level,
        "warning_level": to_float(raw.get("warning_level")),
        "danger_level": to_float(raw.get("danger_level")),
        "trend": (raw.get("steady") or "").strip() or None,
        "status": (raw.get("status") or "").strip() or None,
        "measured_at": measured_at,
    }


def severity(s):
    level = s["water_level"]
    if s["danger_level"] is not None and level >= s["danger_level"]:
        return "DANGER"
    if s["warning_level"] is not None and level >= s["warning_level"]:
        return "WARNING"
    return "NORMAL"


def margin(s):
    if s["warning_level"] is None:
        return None
    return s["water_level"] - s["warning_level"]


def is_stale(station, now):
    if not station["measured_at"]:
        return True
    try:
        measured = datetime.fromisoformat(station["measured_at"])
    except ValueError:
        return True
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=timezone.utc)
    return now - measured > timedelta(minutes=STALE_MINUTES)


def build_snapshot():
    req = urllib.request.Request(API_URL, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.load(resp)
    if payload.get("status") != "success":
        raise RuntimeError("API returned status: " + str(payload.get("status")))
    stations, skipped = [], 0
    for raw in payload.get("data", []):
        parsed = parse_station(raw)
        if parsed:
            stations.append(parsed)
        else:
            skipped += 1
    now = datetime.now(timezone.utc)
    rows = []
    for s in stations:
        rows.append({
            "id": s["station_id"],
            "name": s["name"],
            "basin": s["basin"],
            "district": s["district"],
            "water_level": s["water_level"],
            "warning_level": s["warning_level"],
            "danger_level": s["danger_level"],
            "trend": s["trend"],
            "status": s["status"],
            "measured_at": s["measured_at"],
            "measured_at_npt": to_npt(s["measured_at"]),
            "stale": is_stale(s, now),
            "severity": severity(s),
            "margin_vs_warning": margin(s),
            "station_url": STATION_PAGE.format(s["station_id"]),
        })
    rank = {"DANGER": 0, "WARNING": 1, "NORMAL": 2}
    rows.sort(key=lambda r: (
        rank.get(r["severity"], 2),
        -(r["margin_vs_warning"]
          if r["margin_vs_warning"] is not None else float("-inf")),
        r["name"],
    ))
    return {
        "generated_at_utc": now.isoformat(),
        "generated_at_npt": now.astimezone(NPT).strftime("%Y-%m-%d %H:%M NPT"),
        "source": "Department of Hydrology and Meteorology (DHM), "
                  "Government of Nepal",
        "source_url": "https://dhm.gov.np/",
        "endpoint": API_URL,
        "reporting": len(rows),
        "skipped": skipped,
        "fresh": sum(1 for r in rows if not r["stale"]),
        "stale": sum(1 for r in rows if r["stale"]),
        "warning": sum(1 for r in rows if r["severity"] == "WARNING"),
        "danger": sum(1 for r in rows if r["severity"] == "DANGER"),
        "stale_minutes": STALE_MINUTES,
        "stations": rows,
    }


def main(argv):
    out_path = argv[1] if len(argv) > 1 else "data/rivers.json"
    try:
        snapshot = build_snapshot()
    except Exception as e:
        print("Could not fetch DHM data: " + str(e), file=sys.stderr)
        return 1
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"Wrote {out_path}: {snapshot['reporting']} reporting, "
          f"{snapshot['fresh']} fresh, {snapshot['stale']} stale, "
          f"{snapshot['warning']} warning, {snapshot['danger']} danger.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
