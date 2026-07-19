#!/usr/bin/env python3
"""Audit whether every workflow layer uses the latest available data."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(run_dir):
    required = {
        "filings": run_dir / "filings.json",
        "financials": run_dir / "financials.json",
        "technicals": run_dir / "technicals.json",
        "macro": run_dir / "macro.json",
        "news_catalysts": run_dir / "news-catalysts.json",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "BLOCK_ACTION",
            "action_allowed": False,
            "missing_layers": missing,
            "gates": {},
        }
    layers = {name: read(path) for name, path in required.items()}
    technical_items = layers["technicals"].get("results", [layers["technicals"]])
    primary = technical_items[0] if technical_items else {}
    gates = {
        "sec_index_current": layers["filings"].get("freshness", {}).get("status") == "CURRENT_INDEX",
        "applicable_filing_present": bool(layers["filings"].get("quality_gate_passed")),
        "financial_period_current": layers["financials"].get("freshness", {}).get("status") == "CURRENT",
        "current_quote_checked": bool(layers["financials"].get("freshness", {}).get("latest_quote_checked")),
        "market_price_current": bool(primary.get("freshness", {}).get("is_latest_available")),
        "macro_current": layers["macro"].get("freshness", {}).get("status") == "CURRENT",
        "current_vix_present": bool(layers["macro"].get("market_snapshot", {}).get("vix", {}).get("value")),
        "news_within_7_days": layers["news_catalysts"].get("freshness", {}).get("status") == "CURRENT",
    }
    warnings = []
    warnings.extend(layers["filings"].get("warnings", []))
    warnings.extend(layers["financials"].get("warnings", []))
    warnings.extend(layers["news_catalysts"].get("warnings", []))
    normalization_required = bool(layers["financials"].get("valuation", {}).get("normalization_required"))
    if normalization_required:
        warnings.append("Valuation requires normalized earnings; GAAP PE alone is not decision-grade")
    allowed = all(gates.values())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "CURRENT" if allowed else "BLOCK_ACTION",
        "action_allowed": allowed,
        "valuation_conclusion_allowed": not normalization_required,
        "gates": gates,
        "warnings": list(dict.fromkeys(warnings)),
        "as_of": {
            "market_price": primary.get("freshness", {}).get("data_date"),
            "financial_period": layers["financials"].get("freshness", {}).get("official_period"),
            "financial_filing": layers["financials"].get("freshness", {}).get("official_filing_date"),
            "vix": layers["macro"].get("market_snapshot", {}).get("vix", {}).get("last_trade_time"),
            "treasury_yields": layers["macro"].get("market_snapshot", {}).get("treasury_yields", {}).get("date"),
            "newest_news": layers["news_catalysts"].get("freshness", {}).get("newest_news_at"),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = audit(Path(args.run_dir))
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Success! Freshness audit written to: {target.resolve()}")
        return 0 if result["action_allowed"] else 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
