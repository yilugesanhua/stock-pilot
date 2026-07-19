#!/usr/bin/env python3
"""Run the deterministic analysis and report pipeline on synthetic fixtures."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "skill" / "stock-pilot" / "scripts" / "stock_pilot.py"
FIXTURE = ROOT / "examples" / "offline-googl"


def load_stock_pilot():
    sys.path.insert(0, str(MODULE.parent))
    spec = importlib.util.spec_from_file_location("stock_pilot_offline_smoke", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    stock_pilot = load_stock_pilot()
    with tempfile.TemporaryDirectory() as directory:
        run_dir = Path(directory) / "GOOGL"
        shutil.copytree(FIXTURE, run_dir)

        quality = stock_pilot.audit(run_dir)
        if not quality.get("action_allowed"):
            raise RuntimeError(f"Offline fixture failed quality gates: {quality}")
        stock_pilot.dump(run_dir / "quality.json", quality)

        analysis = stock_pilot.analyze(run_dir)
        stock_pilot.report(run_dir)

        report_json = stock_pilot.load(run_dir / "report.json")
        report_md = (run_dir / "report.md").read_text(encoding="utf-8")
        standard = report_json.get("standard_report", {})

        if analysis.get("ticker") != "GOOGL":
            raise RuntimeError("Offline analysis lost the fixture ticker")
        if standard.get("schema_version") != "1.0":
            raise RuntimeError("Offline report schema version is invalid")
        for heading in ("## 直接建议", "## Bull / Base / Bear", "## 数据新鲜度", "## 来源"):
            if heading not in report_md:
                raise RuntimeError(f"Offline report is missing {heading}")

        # Keep the CI status line ASCII so Windows cp1252 runners cannot fail
        # after the actual report pipeline has already completed.
        print("Offline smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
