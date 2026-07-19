import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).parents[1] / "scripts" / "stock_pilot.py"
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("stock_pilot", MODULE)
stock_pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock_pilot)


class ContractTests(unittest.TestCase):
    def test_ticker_normalization(self):
        self.assertEqual(stock_pilot.ticker("nvda.us"), "NVDA")
        with self.assertRaises(ValueError):
            stock_pilot.ticker("NVDA; rm")

    def test_python_child_errors_remain_utf8(self):
        with self.assertRaisesRegex(RuntimeError, "连接被远端关闭"):
            stock_pilot.execute([
                sys.executable,
                "-c",
                "import sys; print('连接被远端关闭', file=sys.stderr); raise SystemExit(1)",
            ])

    def test_technical_symbols_exclude_unused_benchmarks(self):
        self.assertEqual(stock_pilot.technical_symbols("META", "XLC"), "META,SPY,XLC")
        self.assertEqual(stock_pilot.technical_symbols("TEST", "SPY"), "TEST,SPY")

    def test_longbridge_check_requires_a_usable_session(self):
        self.assertTrue(stock_pilot.longbridge_check_authenticated({
            "session": {"token": "valid", "detail": "authenticated"}
        }))
        self.assertFalse(stock_pilot.longbridge_check_authenticated({
            "session": {"token": "valid", "detail": "api error: code=401102: token verification failed"}
        }))

    def test_public_fallback_collapses_longbridge_noise(self):
        warnings = stock_pilot.collapse_provider_warnings([
            "Longbridge quote unavailable; current price uses Yahoo public chart: 401102",
            "Longbridge news unavailable; Google News RSS metadata used: 401102",
            "Public quote fallback failed: timeout",
        ])
        self.assertEqual(sum("Longbridge" in item for item in warnings), 1)
        self.assertTrue(any("无需券商开户" in item for item in warnings))
        self.assertTrue(any("公共行情回退失败" in item or "Public quote fallback failed" in item for item in warnings))

    def test_audit_blocks_missing_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            result = stock_pilot.audit(Path(directory))
        self.assertFalse(result["action_allowed"])
        self.assertEqual(result["status"], "BLOCK_ACTION")

    def test_wait_targets_are_above_confirmation(self):
        entry, stop, targets, rr = stock_pilot.trade_levels("等待", 202.81, 7.5, 191.14, [213.81, 236.26])
        self.assertGreater(targets[0], entry[0])
        self.assertGreaterEqual(targets[1] - targets[0], 1.5 * 7.5 - 0.01)
        self.assertLess(stop, entry[0])
        self.assertGreater(rr, 0)

    def test_unknown_symbol_writes_safe_report(self):
        with tempfile.TemporaryDirectory() as directory:
            stock_pilot.unavailable_report("SPACX", "2-8w", Path(directory), "symbol not found")
            result = stock_pilot.load(Path(directory) / "analysis.json")
            self.assertEqual(result["recommendation"], "数据不足")
            self.assertFalse(result["quality"]["action_allowed"])
            report = (Path(directory) / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Bull / Base / Bear", report)
            self.assertIn("## 数据新鲜度", report)
            self.assertIn("## 来源", report)

    def test_failure_categories_are_explicit(self):
        cases = (
            ("HXSCL", "UNSUPPORTED_VENUE", "不在当前美国交易所/ADR覆盖范围"),
            ("NVDA", "PROVIDER_TEMPORARY_ERROR", "不是“代码不存在”"),
        )
        for code, category, expected in cases:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                stock_pilot.unavailable_report(code, "2-8w", path, "test failure", category)
                quality = stock_pilot.load(path / "quality.json")
                analysis = stock_pilot.load(path / "analysis.json")
                self.assertEqual(quality["failure_category"], category)
                self.assertIn(expected, quality["warnings"][0] + analysis["reason"])

    def test_known_sk_hynix_alias_is_unsupported(self):
        with self.assertRaises(stock_pilot.UnsupportedVenueError):
            stock_pilot.company_and_sector("HXSCL")

    def test_unrecognized_symbol_has_a_distinct_exception_type(self):
        self.assertTrue(issubclass(stock_pilot.UnrecognizedSymbolError, ValueError))
        self.assertFalse(issubclass(stock_pilot.UnrecognizedSymbolError, stock_pilot.TemporarySourceError))

    def test_reason_contains_evidence_for_an_actionable_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            stock_pilot.dump(path / "manifest.json", {"ticker": "TEST", "horizon": "2-8w", "sector_etf": "SPY"})
            stock_pilot.dump(path / "financials.json", {"selected_latest": {"period": "2026.Q2"}, "valuation": {}})
            stock_pilot.dump(path / "technicals.json", {"results": [{"price": {"current": 100}, "price_structure": {"sma20": 99, "sma50": 98, "sma200": 95, "support": {"a": 95}, "resistance": {"a": 105}, "returns_pct": {}}, "indicators": {"atr": {"value": 2}, "rsi": {"value": 55}, "macd": {"histogram": 1}}, "relative_strength_pct_points": {"SPY": {"20": 1}}, "freshness": {"is_latest_available": True}}]})
            stock_pilot.dump(path / "macro.json", {"market_snapshot": {"vix": {"value": 18, "change_pct": 0}, "treasury_yields": {}}})
            stock_pilot.dump(path / "news-catalysts.json", {"catalysts": {}, "news": []})
            stock_pilot.dump(path / "quality.json", {"status": "CURRENT", "action_allowed": True, "valuation_conclusion_allowed": True, "gates": {}, "warnings": []})
            result = stock_pilot.analyze(path)
            self.assertIn("技术面：", result["reason"])
            self.assertIn("VIX", result["reason"])
            self.assertIn("2026.Q2", result["reason"])
            self.assertIn("风险收益比", result["reason"])

    def test_sector_overrides_beat_ambiguous_company_profile(self):
        self.assertEqual(stock_pilot.TICKER_SECTOR_OVERRIDES["MSFT"], "XLK")
        self.assertEqual(stock_pilot.TICKER_SECTOR_OVERRIDES["META"], "XLC")
        self.assertEqual(stock_pilot.TICKER_SECTOR_OVERRIDES["BRK.B"], "XLF")

    def test_missing_layer_quality_preserves_collection_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            stock_pilot.dump(path / "collection-errors.json", {"financials": "SEC timeout"})
            result = stock_pilot.audit(path)
            self.assertEqual(result["collection_errors"]["financials"], "SEC timeout")
            self.assertTrue(any("financials" in warning for warning in result["warnings"]))

    def test_missing_layer_quality_preserves_available_as_of_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            stock_pilot.dump(path / "technicals.json", {
                "results": [{"freshness": {"data_date": "2026-07-17"}}]
            })
            stock_pilot.dump(path / "collection-errors.json", {"news": "HTTP 400"})
            result = stock_pilot.audit(path)
            self.assertEqual(result["as_of"]["market_price"], "2026-07-17")

    def test_common_dynamic_warnings_are_rendered_in_chinese(self):
        warning = stock_pilot.human_warning("Vendor PE 47.21 uses a stale denominator; recomputed TTM PE is 51.44")
        self.assertIn("供应商 PE", warning)
        self.assertIn("51.44", warning)
        self.assertIn("官方文件财务期 2026.Q2 覆盖了滞后的结构化财务期 2026.H1", stock_pilot.human_warning(
            "Official filing 2026.Q2 overrides lagging structured period 2026.H1"
        ))

    def test_recent_ipo_report_labels_expected_price_and_decision_model(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            stock_pilot.dump(run_dir / "manifest.json", {"ticker": "SPCX", "horizon": "2-8w"})
            stock_pilot.dump(run_dir / "financials.json", {"selected_latest": {
                "company_stage": "recent_ipo",
                "expected_ipo_price_usd": 135.0,
                "expected_ipo_price_status": "expected_not_final",
            }})
            stock_pilot.dump(run_dir / "technicals.json", {"results": [{
                "price": {"current": 123.99},
                "price_structure": {},
                "indicators": {},
            }]})
            stock_pilot.dump(run_dir / "macro.json", {"series": [{
                "series_id": "FEDFUNDS",
                "latest_value": 3.63,
                "latest_date": "2026-06-01",
                "source": "fred_api",
                "freshness": {"status": "CURRENT"},
            }]})
            stock_pilot.dump(run_dir / "analysis.json", {
                "ticker": "SPCX",
                "horizon": "2-8w",
                "recommendation": "回避",
                "technical": {"price": 123.99, "short_history": True},
                "quality": {"status": "CURRENT", "action_allowed": True, "gates": {}, "as_of": {}},
            })
            stock_pilot.report(run_dir)
            report_json = stock_pilot.load(run_dir / "report.json")["standard_report"]
            observation = report_json["recent_ipo_observation"]
            self.assertEqual(observation["expected_ipo_price_usd"], 135.0)
            self.assertEqual(observation["current_price_vs_expected_ipo_pct"], -8.16)
            self.assertFalse(report_json["decision_model"]["equal_weighted"])
            body = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("## 决策模型", body)
            self.assertIn("## 新股观察", body)
            self.assertIn("预期值，不是最终定价", body)
            self.assertIn("未确认 / N/A", body)
            self.assertIn("联邦基金利率", body)
            self.assertIn("3.63%", body)
            self.assertEqual(report_json["macro"]["fred_series"]["FEDFUNDS"]["date"], "2026-06-01")

    def test_blocked_report_has_standard_schema_and_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            stock_pilot.dump(
                run_dir / "analysis.json",
                {
                    "ticker": "TEST",
                    "horizon": "2-8w",
                    "recommendation": "数据不足",
                    "reason": "缺少核心数据",
                    "quality": {
                        "status": "BLOCK_ACTION",
                        "action_allowed": False,
                        "gates": {"market_price_current": False},
                        "warnings": ["测试警告"],
                        "as_of": {},
                    },
                },
            )
            stock_pilot.report(run_dir)
            body = (run_dir / "report.md").read_text(encoding="utf-8")
            report_json = stock_pilot.load(run_dir / "report.json")
            self.assertEqual(report_json["standard_report"]["schema_version"], "1.0")
            for heading in (
                "## 直接建议",
                "## 执行条件",
                "## 基本面",
                "## 估值",
                "## 技术面",
                "## 宏观与市场风险",
                "## 最近 7 天新闻",
                "## 未来 8 周催化剂",
                "## Bull / Base / Bear",
                "## 风险与数据限制",
                "## 数据新鲜度",
                "## 来源",
            ):
                self.assertIn(heading, body)

    def test_report_lists_public_source_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            stock_pilot.dump(run_dir / "manifest.json", {"ticker": "AAPL", "horizon": "2-8w"})
            stock_pilot.dump(run_dir / "analysis.json", {
                "ticker": "AAPL", "horizon": "2-8w", "recommendation": "等待",
                "quality": {"status": "CURRENT", "action_allowed": True, "gates": {}, "as_of": {}},
            })
            stock_pilot.dump(run_dir / "technicals.json", {"results": [{"price": {"current": 100}, "freshness": {}}]})
            stock_pilot.dump(run_dir / "news-catalysts.json", {"news": [], "public_news_url": "https://news.google.com/rss/search?q=AAPL"})
            stock_pilot.report(run_dir)
            body = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("query1.finance.yahoo.com/v8/finance/chart/AAPL", body)
            self.assertNotIn("query1.finance.yahoo.com/v8/finance/chart/AAPL.US", body)
            self.assertIn("news.google.com/rss/search?q=AAPL", body)

    def test_report_distinguishes_current_and_confirmation_risk_reward(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            stock_pilot.dump(run_dir / "manifest.json", {"ticker": "TEST", "horizon": "2-8w"})
            stock_pilot.dump(run_dir / "analysis.json", {
                "ticker": "TEST", "horizon": "2-8w", "recommendation": "等待",
                "risk_reward_to_target1": 3.06,
                "current_entry_risk_reward_to_target1": 3.22,
                "confirmation_risk_reward_to_target1": 3.06,
                "quality": {"status": "CURRENT", "action_allowed": True, "gates": {}, "as_of": {}},
            })
            stock_pilot.report(run_dir)
            body = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("| 当前价入场风险收益比 | 3.22 |", body)
            self.assertIn("| 确认突破风险收益比 | 3.06 |", body)

    def test_report_does_not_claim_valuation_when_no_pe_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            stock_pilot.dump(run_dir / "manifest.json", {"ticker": "TEST", "horizon": "2-8w"})
            stock_pilot.dump(run_dir / "analysis.json", {
                "ticker": "TEST", "horizon": "2-8w", "recommendation": "等待",
                "valuation": {}, "valuation_conclusion_allowed": True,
                "quality": {"status": "CURRENT", "action_allowed": True, "gates": {}, "as_of": {}},
            })
            stock_pilot.report(run_dir)
            body = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("未取得可用 PE 数据，未下估值结论", body)

    def test_avoid_report_does_not_publish_buy_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            stock_pilot.dump(run_dir / "manifest.json", {"ticker": "TEST", "horizon": "2-8w"})
            stock_pilot.dump(
                run_dir / "analysis.json",
                {
                    "ticker": "TEST",
                    "horizon": "2-8w",
                    "recommendation": "回避",
                    "entry_or_confirmation": [10, 11],
                    "invalidation_stop": 9,
                    "targets": [12, 13],
                    "risk_reward_to_target1": 2,
                    "quality": {"status": "CURRENT", "action_allowed": True, "gates": {}, "as_of": {}},
                },
            )
            stock_pilot.report(run_dir)
            payload = stock_pilot.load(run_dir / "report.json")["standard_report"]
            self.assertIsNone(payload["action_plan"]["entry_or_confirmation"])
            self.assertEqual(payload["action_plan"]["targets"], [])
            self.assertIn("不支持新资金介入", payload["action_plan"]["note"])


if __name__ == "__main__":
    unittest.main()
