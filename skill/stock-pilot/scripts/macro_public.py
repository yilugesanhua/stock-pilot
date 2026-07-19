#!/usr/bin/env python3
"""Public macro adapter using FRED CSV, Cboe delayed VIX and Treasury XML."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import pandas as pd


SERIES = ("VIXCLS", "DGS2", "DGS10", "T10Y2Y", "FEDFUNDS", "CPIAUCSL", "CPILFESL", "UNRATE", "PAYEMS", "BAMLH0A0HYM2")
MAX_AGE_DAYS = {name: 4 for name in ("VIXCLS", "DGS2", "DGS10", "T10Y2Y", "BAMLH0A0HYM2")}
MAX_AGE_DAYS.update({name: 50 for name in ("FEDFUNDS", "CPIAUCSL", "CPILFESL", "UNRATE", "PAYEMS")})
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"
TREASURY_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"


def get(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "stock-pilot-public-macro/0.1"})
    last = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"Request failed: {last}")


def get_fred(url: str) -> bytes:
    """Use curl first because some Windows proxy stacks reset Python TLS."""
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        try:
            completed = subprocess.run(
                [curl, "--noproxy", "*", "--silent", "--show-error", "--fail", "--location", "--max-time", "20", "--retry", "2", url],
                check=True,
                capture_output=True,
                timeout=60,
            )
            return completed.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return get(url)


def fred(series_id: str, start: str) -> dict:
    url = CSV_URL + "?" + urlencode({"id": series_id, "cosd": start})
    frame = pd.read_csv(StringIO(get_fred(url).decode("utf-8")))
    frame = frame.rename(columns={frame.columns[0]: "date", series_id: "value"})
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["value"])
    if frame.empty:
        raise RuntimeError(f"FRED returned no observations for {series_id}")
    row = frame.iloc[-1]
    return {"series_id": series_id, "latest_date": str(row["date"]), "latest_value": float(row["value"]), "observations": len(frame), "source": "fred_csv", "source_checked_at": datetime.now(timezone.utc).isoformat()}


def freshness(item: dict) -> dict:
    try:
        observed = datetime.strptime(str(item["latest_date"]), "%Y-%m-%d").date()
        age = (datetime.now(timezone.utc).date() - observed).days
    except (KeyError, ValueError):
        return {"status": "MISSING"}
    maximum = MAX_AGE_DAYS.get(item["series_id"], 50)
    return {"status": "CURRENT" if age <= maximum else "STALE", "observation_age_days": age, "max_age_days": maximum}


def vix() -> dict:
    data = json.loads(get(CBOE_URL)).get("data") or {}
    value = float(data.get("current_price") or data.get("close"))
    change = float(data.get("price_change") or 0)
    previous = float(data.get("prev_day_close") or (value - change if change else value))
    return {"value": value, "previous_close": previous, "change": change, "change_pct": round((value / previous - 1) * 100, 4) if previous else 0, "last_trade_time": data.get("last_trade_time"), "source": "cboe_delayed_quote", "source_url": CBOE_URL}


def treasury() -> dict:
    url = TREASURY_URL + "?" + urlencode({"data": "daily_treasury_yield_curve", "field_tdr_date_value": str(datetime.now().year)})
    root = ElementTree.fromstring(get(url))
    namespace = {"d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
    rows = []
    for properties in root.findall(".//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties"):
        date_node = properties.find("d:NEW_DATE", namespace)
        two = properties.find("d:BC_2YEAR", namespace)
        ten = properties.find("d:BC_10YEAR", namespace)
        if date_node is not None and two is not None and ten is not None:
            rows.append((date_node.text[:10], float(two.text), float(ten.text)))
    if not rows:
        raise RuntimeError("Treasury XML returned no 2Y/10Y rows")
    date, two, ten = max(rows, key=lambda row: row[0])
    return {"date": date, "us2y_pct": two, "us10y_pct": ten, "yield_curve_10y2y_pct": round(ten - two, 4), "source": "us_treasury_daily_yield_curve", "source_url": url}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", default=",".join(SERIES))
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--force-csv", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    results = []
    failures = []
    for series_id in [item.strip().upper() for item in args.series.split(",") if item.strip()]:
        try:
            item = fred(series_id, args.start)
            item["freshness"] = freshness(item)
            results.append(item)
            if item["freshness"]["status"] != "CURRENT":
                failures.append(series_id)
        except Exception as exc:
            failures.append(series_id)
            results.append({"series_id": series_id, "error": str(exc), "freshness": {"status": "MISSING"}})
    try:
        current_vix = vix()
    except Exception as exc:
        current_vix = {"error": str(exc)}
    try:
        current_treasury = treasury()
    except Exception as exc:
        current_treasury = {"error": str(exc)}
    output = {"generated_at": datetime.now(timezone.utc).isoformat(), "series": results, "market_snapshot": {"vix": current_vix, "treasury_yields": current_treasury}, "freshness": {"status": "CURRENT" if not failures and "error" not in current_vix and "error" not in current_treasury else "STALE_OR_MISSING", "failures": failures}}
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        print(f"Success! Macro data written to: {args.output}")
    else:
        print(rendered)
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
