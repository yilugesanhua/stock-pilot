import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


technical = load("technical_public")
macro = load("macro_public")
stock_pilot = load("stock_pilot")


class PublicAdapterTests(unittest.TestCase):
    def technical_frame(self):
        dates = pd.date_range("2025-01-01", periods=220, freq="B", tz="UTC")
        close = pd.Series([100 + index * 0.5 for index in range(220)])
        return pd.DataFrame(
            {
                "date": dates,
                "open": close - 0.25,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1_000_000,
            }
        )

    def test_public_technical_schema_and_indicators(self):
        frame = self.technical_frame()
        date = str(frame["date"].iloc[-1].date())
        result = technical.result("TEST", frame, date, "1y")
        self.assertTrue(result["freshness"]["is_latest_available"])
        self.assertIsNotNone(result["price_structure"]["sma200"])
        self.assertGreater(result["indicators"]["rsi"]["value"], 50)
        self.assertIn("histogram", result["indicators"]["macd"])
        self.assertGreater(result["indicators"]["atr"]["value"], 0)

    def test_fred_csv_and_freshness(self):
        today = datetime.now(timezone.utc).date().isoformat()
        payload = f"DATE,DGS10\n{today},4.25\n".encode()
        with mock.patch.object(macro, "get_fred", return_value=payload):
            item = macro.fred("DGS10", "2000-01-01")
        self.assertEqual(item["latest_value"], 4.25)
        self.assertEqual(macro.freshness(item)["status"], "CURRENT")

    def test_cboe_and_treasury_parsers(self):
        vix_payload = json.dumps({"data": {"current_price": 18.0, "prev_day_close": 17.5, "last_trade_time": "2099-01-01"}}).encode()
        treasury_payload = b'''<feed xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><entry><content><m:properties><d:NEW_DATE>2099-01-01T00:00:00</d:NEW_DATE><d:BC_2YEAR>3.9</d:BC_2YEAR><d:BC_10YEAR>4.1</d:BC_10YEAR></m:properties></content></entry></feed>'''
        with mock.patch.object(macro, "get", side_effect=[vix_payload, treasury_payload]):
            vix = macro.vix()
            treasury = macro.treasury()
        self.assertEqual(vix["value"], 18.0)
        self.assertEqual(treasury["yield_curve_10y2y_pct"], 0.2)

    def test_orchestrator_uses_bundled_public_adapters(self):
        self.assertEqual(stock_pilot.DEPENDENCIES["technicals"].name, "technical_public.py")
        self.assertEqual(stock_pilot.DEPENDENCIES["macro"].name, "macro_public.py")
        self.assertTrue(stock_pilot.DEPENDENCIES["technicals"].exists())
        self.assertTrue(stock_pilot.DEPENDENCIES["macro"].exists())


if __name__ == "__main__":
    unittest.main()
