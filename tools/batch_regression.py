#!/usr/bin/env python3
"""Run Stock Pilot across diverse securities and validate every report contract."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_UNIVERSE = {
    "AAPL": "大型科技/硬件",
    "MSFT": "大型科技/软件",
    "NVDA": "半导体",
    "GOOGL": "互联网广告",
    "AMZN": "电商/云计算",
    "JPM": "大型银行",
    "BRK.B": "保险/多元控股/点号代码",
    "XOM": "综合能源",
    "JNJ": "综合医疗",
    "LLY": "创新药",
    "WMT": "必需消费",
    "COST": "会员零售",
    "HD": "可选消费",
    "CAT": "工业机械",
    "NEE": "公用事业",
    "PLD": "工业地产REIT",
    "LIN": "材料/工业气体",
    "TSM": "中国台湾ADR/晶圆代工",
    "ASML": "荷兰ADR/半导体设备",
    "NVO": "丹麦ADR/医药",
    "BABA": "中国ADR/电商",
    "PDD": "中国ADR/电商",
    "RIVN": "亏损成长/汽车",
    "COIN": "高波动金融科技",
    "META": "社交媒体/互联网广告",
    "AVGO": "半导体/基础设施软件",
    "AMD": "半导体/处理器",
    "MU": "半导体/存储",
    "ORCL": "企业软件/云计算",
    "CRM": "企业软件/SaaS",
    "NFLX": "流媒体",
    "DIS": "传统媒体/主题公园",
    "V": "支付网络",
    "MA": "支付网络",
    "BAC": "大型银行",
    "GS": "投资银行",
    "MS": "投资银行/财富管理",
    "AXP": "信用卡/消费金融",
    "PGR": "财产保险",
    "CB": "全球保险",
    "CVX": "综合能源",
    "COP": "油气勘探生产",
    "SHEL": "英国ADR/综合能源",
    "PBR": "巴西ADR/国有能源",
    "ABBV": "生物制药",
    "MRK": "创新药",
    "PFE": "大型制药",
    "UNH": "医疗保险",
    "NVS": "瑞士ADR/制药",
    "AZN": "英国ADR/制药",
    "KO": "饮料/必需消费",
    "PEP": "食品饮料/必需消费",
    "PG": "日化/必需消费",
    "MCD": "连锁餐饮",
    "DE": "农业机械",
    "BA": "航空制造",
    "SO": "公用事业",
    "DUK": "公用事业",
    "AMT": "通信基础设施REIT",
    "O": "零售地产REIT",
    "PLTR": "数据分析/高增长软件",
    "IBM": "企业IT/软件",
    "CSCO": "网络设备",
    "ADBE": "创意软件",
    "NOW": "企业SaaS",
    "PANW": "网络安全",
    "CRWD": "云网络安全",
    "SHOP": "加拿大电商软件",
    "UBER": "出行/平台经济",
    "ABNB": "旅游平台",
    "QCOM": "半导体/通信芯片",
    "TXN": "模拟芯片",
    "AMAT": "半导体设备",
    "LRCX": "半导体设备",
    "KLAC": "半导体设备",
    "ARM": "英国ADR/芯片架构",
    "C": "大型银行",
    "WFC": "大型银行",
    "SCHW": "券商/财富管理",
    "BLK": "资产管理",
    "BX": "另类资管",
    "CME": "交易所/衍生品",
    "ICE": "交易所/金融数据",
    "HOOD": "互联网券商",
    "PYPL": "支付科技",
    "NU": "巴西数字银行",
    "TMO": "生命科学工具",
    "AMGN": "生物制药",
    "GILD": "生物制药",
    "BMY": "大型制药",
    "ISRG": "医疗器械/机器人",
    "MDT": "医疗器械",
    "SNY": "法国ADR/制药",
    "GSK": "英国ADR/制药",
    "TGT": "折扣零售",
    "LOW": "家居零售",
    "TJX": "折扣零售",
    "NKE": "运动消费",
    "SBUX": "连锁咖啡",
    "CMG": "连锁餐饮",
    "BKNG": "在线旅游",
    "RCL": "邮轮旅游",
    "GE": "航空/工业",
    "RTX": "航空航天/防务",
    "LMT": "防务承包商",
    "HON": "工业自动化",
    "UPS": "物流运输",
    "UNP": "铁路运输",
    "WM": "环保服务",
    "ETN": "电气设备",
    "AEP": "公用事业",
    "CEG": "核电/公用事业",
    "EQIX": "数据中心REIT",
    "SPG": "商业地产REIT",
    "WELL": "医疗地产REIT",
    "PSA": "仓储地产REIT",
    "NEM": "黄金矿业",
    "FCX": "铜矿",
    "BHP": "澳大利亚ADR/矿业",
    "RIO": "英国ADR/矿业",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def finite_numbers(value, path="root"):
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            errors.extend(finite_numbers(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(finite_numbers(item, f"{path}[{index}]"))
    elif isinstance(value, float) and not math.isfinite(value):
        errors.append(f"non-finite number at {path}")
    return errors


def validate_run(ticker: str, category: str, run_dir: Path, returncode: int, stderr: str):
    errors = []
    required = ("analysis.json", "quality.json", "report.json", "report.md")
    for name in required:
        if not (run_dir / name).exists():
            errors.append(f"missing {name}")
    if errors:
        return {"ticker": ticker, "category": category, "status": "FAIL", "errors": errors, "stderr": stderr[-2000:]}

    try:
        analysis = read_json(run_dir / "analysis.json")
        quality = read_json(run_dir / "quality.json")
        report = read_json(run_dir / "report.json")
        markdown = (run_dir / "report.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"ticker": ticker, "category": category, "status": "FAIL", "errors": [f"artifact parse failure: {exc}"]}

    errors.extend(finite_numbers(report))
    artifact_text = "\n".join((markdown, json.dumps(quality, ensure_ascii=False), json.dumps(report, ensure_ascii=False)))
    if "\ufffd" in artifact_text or re.search(r"\b(?:Traceback|NoneType|AttributeError|KeyError|TypeError):", artifact_text):
        errors.append("artifact contains traceback, null-type exception, or replacement-character corruption")
    collection_errors = quality.get("collection_errors") or {}
    collection_text = json.dumps(collection_errors, ensure_ascii=False)
    if re.search(r"Traceback|NoneType|AttributeError|KeyError|TypeError:", collection_text):
        errors.append("collection error contains an unhandled code exception")
    if returncode != 0:
        errors.append(f"runner exited {returncode}: {stderr[-500:]}")
    standard = report.get("standard_report") or {}
    if standard.get("ticker") != ticker:
        errors.append(f"ticker mismatch: {standard.get('ticker')}")
    if standard.get("status") != quality.get("status"):
        errors.append("quality status mismatch")
    if bool(standard.get("action_allowed")) != bool(quality.get("action_allowed")):
        errors.append("action permission mismatch")
    if quality.get("action_allowed") and not all((quality.get("gates") or {}).values()):
        errors.append("action allowed despite failed freshness gate")
    if not quality.get("action_allowed"):
        plan = standard.get("action_plan") or {}
        if plan.get("entry_or_confirmation") or plan.get("targets") or plan.get("invalidation_stop") is not None:
            errors.append("blocked report leaked execution levels")

    technicals_path = run_dir / "technicals.json"
    if quality.get("action_allowed") and not technicals_path.exists():
        errors.append("actionable run missing technicals.json")
    elif technicals_path.exists():
        technicals = read_json(technicals_path)
        primary = (technicals.get("results") or [{}])[0]
        freshness = primary.get("freshness") or {}
        price = ((primary.get("price") or {}).get("current"))
        if price is not None and freshness.get("is_latest_available") is not True:
            errors.append("report does not use latest completed US session")
        if quality.get("action_allowed") and (not isinstance(price, (int, float)) or price <= 0):
            errors.append("actionable run has invalid market price")
        if quality.get("as_of", {}).get("market_price") is not None and quality.get("as_of", {}).get("market_price") != freshness.get("data_date"):
            errors.append("market-price as-of date mismatch")

    plan = standard.get("action_plan") or {}
    if quality.get("action_allowed") and standard.get("recommendation") not in {"回避", "数据不足"}:
        if plan.get("confirmation_risk_reward_to_target1") != analysis.get("risk_reward_to_target1"):
            errors.append("confirmation risk/reward mismatch")
        if plan.get("current_entry_risk_reward_to_target1") != analysis.get("current_entry_risk_reward_to_target1"):
            errors.append("current-entry risk/reward mismatch")

    sources = standard.get("sources") or []
    urls = [str(item.get("url") or "") for item in sources]
    if quality.get("action_allowed"):
        for source_name, pattern in (
            ("SEC", r"https://(?:www\.)?(?:sec\.gov|data\.sec\.gov)/"),
            ("Yahoo", r"https://query1\.finance\.yahoo\.com/"),
            ("Cboe", r"https://cdn\.cboe\.com/"),
            ("Treasury", r"https://home\.treasury\.gov/"),
        ):
            if not any(re.match(pattern, url) for url in urls):
                errors.append(f"missing real {source_name} source URL")
    if any(re.search(r"/chart/[^/?]+\.US(?:\?|$)", url) for url in urls):
        errors.append("Yahoo source URL contains invalid internal .US suffix")

    news_path = run_dir / "news-catalysts.json"
    if news_path.exists():
        news = read_json(news_path)
        relevant = [item for item in news.get("news", []) if item.get("relevant")]
        headline_clue = any(re.search(r"\b(?:ahead of|before|upcoming|next)\b.{0,24}\bearnings\b|\bearnings\b.{0,24}\b(?:ahead|upcoming|next)\b", str(item.get("title", "")), re.I) for item in relevant)
        catalysts = news.get("catalysts") or {}
        if headline_clue and not catalysts.get("confirmed") and not catalysts.get("estimated"):
            errors.append("earnings headline clue missing from estimated catalysts")

    for heading in ("## 直接建议", "## 执行条件", "## 数据新鲜度", "## 来源"):
        if heading not in markdown:
            errors.append(f"report missing heading {heading}")

    return {
        "ticker": ticker,
        "category": category,
        "status": "PASS" if not errors else "FAIL",
        "quality_status": quality.get("status"),
        "action_allowed": quality.get("action_allowed"),
        "recommendation": analysis.get("recommendation"),
        "market_date": quality.get("as_of", {}).get("market_price"),
        "financial_period": quality.get("as_of", {}).get("financial_period"),
        "newest_news": quality.get("as_of", {}).get("newest_news"),
        "errors": errors,
    }


def run_one(script: Path, root: Path, ticker: str, category: str, timeout: int):
    run_dir = root / ticker.replace(".", "_")
    command = [sys.executable, str(script), "run", "--ticker", ticker, "--horizon", "2-8w", "--output", str(run_dir)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return validate_run(ticker, category, run_dir, completed.returncode, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        return {"ticker": ticker, "category": category, "status": "FAIL", "errors": [f"timeout after {timeout}s"], "stderr": str(exc)}


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--script", default=str(repository / "skill/stock-pilot/scripts/stock_pilot.py"))
    parser.add_argument("--tickers", nargs="*")
    args = parser.parse_args()

    universe = DEFAULT_UNIVERSE if not args.tickers else {ticker.upper(): "自定义" for ticker in args.tickers}
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    script = Path(args.script)
    started = datetime.now(timezone.utc)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(run_one, script, root, ticker, category, args.timeout): ticker
            for ticker, category in universe.items()
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"{result['status']:4} {result['ticker']:6} {result.get('quality_status', 'N/A'):12} {result.get('market_date', 'N/A')}", flush=True)

    results.sort(key=lambda item: list(universe).index(item["ticker"]))
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started.isoformat(),
        "script": str(script),
        "count": len(results),
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] == "FAIL" for item in results),
        "categories": sorted({item["category"] for item in results}),
        "results": results,
    }
    (root / "batch-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("count", "passed", "failed")}), flush=True)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
