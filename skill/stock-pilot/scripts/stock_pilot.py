#!/usr/bin/env python3
"""Stock Pilot orchestration CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from public_data import GOOGLE_NEWS_RSS_URL, YAHOO_CHART_URL, rss_news, sec_company, yahoo_quote

SKILL = Path(__file__).resolve().parents[1]
REPOSITORY = SKILL.parents[1]
LOCAL_SKILLS = REPOSITORY / "dependencies" / "skills"


def dependency_script(skill_name, script_name):
    local = LOCAL_SKILLS / skill_name / "scripts" / script_name
    installed = Path.home() / ".codex" / "skills" / skill_name / "scripts" / script_name
    return local if local.exists() else installed


DEPENDENCIES = {
    "filings": dependency_script("longbridge-content", "filing_evidence.py"),
    "financials": dependency_script("longbridge-fundamentals", "reconcile_financials.py"),
    "technicals": SKILL / "scripts" / "technical_public.py",
    "macro": SKILL / "scripts" / "macro_public.py",
    "news": dependency_script("longbridge-content", "news_catalysts.py"),
}
BASE_BENCHMARKS = ["SPY"]
TICKER_SECTOR_OVERRIDES = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "SOXX", "AVGO": "SOXX",
    "AMZN": "XLY", "TSLA": "XLY", "META": "XLC", "GOOGL": "XLC",
    "GOOG": "XLC", "BRK.B": "XLF", "JPM": "XLF", "V": "XLF",
    "MA": "XLF", "UNH": "XLV", "LLY": "XLV", "JNJ": "XLV",
    "XOM": "XLE", "CVX": "XLE", "HD": "XLY", "COST": "XLP",
    "WMT": "XLP", "TSM": "SOXX", "ASML": "SOXX", "NVO": "XLV",
}


class TemporarySourceError(RuntimeError):
    """A provider/network failure; never present it as an invalid ticker."""


class UnsupportedVenueError(RuntimeError):
    """A real security/venue outside the configured US/ADR coverage."""


class UnrecognizedSymbolError(ValueError):
    """The provider returned successfully but has no US/ADR metadata."""


KNOWN_UNSUPPORTED = {
    "HXSCL": "SK 海力士主上市为韩国 000660.KS；当前 Longbridge 配置不覆盖韩国主板或该 OTC 代码。",
    "HXSCF": "SK 海力士主上市为韩国 000660.KS；当前 Longbridge 配置不覆盖韩国主板或该 OTC 代码。",
    "000660": "SK 海力士主上市为韩国 000660.KS；当前 Longbridge 配置不覆盖韩国主板或该 OTC 代码。",
}
SECTOR_RULES = [
    (("berkshire", "insurance", "reinsurance", "保险", "再保险"), "XLF"),
    (("semiconductor", "foundry", "chip", "gpu", "nvidia", "micro devices", "半导体", "晶圆", "台积电"), "SOXX"),
    (("bank", "banking", "financial holding", "银行", "金融控股"), "XLF"),
    (("oil", "gas", "energy", "petroleum", "原油", "天然气", "能源"), "XLE"),
    (("pharma", "drug", "healthcare", "biotech", "制药", "医疗", "糖尿病"), "XLV"),
    (("software", "cloud", "technology", "computing", "软件", "云计算"), "XLK"),
    (("media", "advertising", "search engine", "communication", "广告", "搜索"), "XLC"),
    (("retail", "automotive", "restaurant", "consumer discretionary", "零售", "汽车"), "XLY"),
    (("food", "beverage", "household", "consumer staples", "食品", "日用品"), "XLP"),
    (("industrial", "aerospace", "machinery", "工业", "航空"), "XLI"),
    (("utility", "electric power", "公用事业", "电力"), "XLU"),
    (("real estate", "reit", "房地产"), "XLRE"),
    (("materials", "mining", "chemical", "矿业", "材料"), "XLB"),
]


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def ticker(value):
    code = value.strip().upper().removesuffix(".US")
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", code):
        raise ValueError(f"Invalid US ticker: {value}")
    return code


def quote_symbol(code):
    return quote(f"{code}.US", safe=".-")


def technical_symbols(code, sector):
    return ",".join(dict.fromkeys([code, *BASE_BENCHMARKS, sector]))


def longbridge_check_authenticated(payload):
    session = payload.get("session") if isinstance(payload, dict) else {}
    detail = str((session or {}).get("detail") or "")
    token = str((session or {}).get("token") or "").lower()
    return token == "valid" and not re.search(r"\berror\b|failed|invalid|expired|401\d+", detail, re.I)


def execute(args, allowed=(0,), timeout=180):
    child_env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=child_env,
    )
    if completed.returncode not in allowed:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"Exit {completed.returncode}")
    return completed.stdout


def py(script, *args):
    return [sys.executable, str(script), *map(str, args)]


def timed_execute(args, timeout=180):
    started = time.perf_counter()
    try:
        execute(args, timeout=timeout)
        return {"status": "ok", "seconds": round(time.perf_counter() - started, 3)}
    except Exception as exc:
        return {
            "status": "error",
            "seconds": round(time.perf_counter() - started, 3),
            "error": str(exc),
        }


def company_and_sector(code):
    if code in KNOWN_UNSUPPORTED:
        raise UnsupportedVenueError(KNOWN_UNSUPPORTED[code])
    longbridge_error = None
    company = {}
    try:
        raw = execute([shutil.which("longbridge"), "company", f"{code}.US", "--format", "json"], timeout=30)
        company = json.loads(raw) or {}
    except (subprocess.TimeoutExpired, RuntimeError, OSError, TypeError) as exc:
        longbridge_error = str(exc)
    if not any(company.get(field) for field in ("company_name", "name")):
        public_company = sec_company(code)
        if public_company:
            company = public_company
            company["data_provider"] = "SEC"
            company["fallback_reason"] = longbridge_error
    if not any(company.get(field) for field in ("company_name", "name")):
        try:
            static = json.loads(execute([shutil.which("longbridge"), "static", f"{code}.US", "--format", "json"], timeout=30)) or []
        except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
            if longbridge_error:
                raise TemporarySourceError(f"Longbridge and SEC identity lookup failed for {code}.US: {longbridge_error}") from exc
            raise TemporarySourceError(f"Longbridge static query failed temporarily: {exc}") from exc
        if isinstance(static, list) and static:
            company = {**company, **static[0]}
    if not any(company.get(field) for field in ("company_name", "name")):
        raise UnrecognizedSymbolError(f"No US/ADR security metadata returned for {code}.US")
    if code in TICKER_SECTOR_OVERRIDES:
        return company, TICKER_SECTOR_OVERRIDES[code]
    text = " ".join(str(company.get(key) or "") for key in ("company_name", "name", "profile")).lower()
    sector = "SPY"
    if "spacex" in text or "space exploration" in text:
        return company, "ARKX"
    for terms, etf in SECTOR_RULES:
        if any(term in text for term in terms):
            sector = etf
            break
    return company, sector


def trade_levels(recommendation, price, atr, support, resistances):
    stop = round(max(0.01, support - 0.5 * atr), 2)
    target1 = round(resistances[0] if resistances else price + 1.5 * atr, 2)
    target2 = round(resistances[-1] if resistances else price + 3 * atr, 2)
    entry = [round(max(support, price - 0.5 * atr), 2), round(price + 0.15 * atr, 2)]
    if recommendation == "等待":
        breakout = resistances[0] if resistances else price + atr
        confirmation = round(breakout + 0.1 * atr, 2)
        entry = [confirmation, confirmation]
        stop = round(max(0.01, breakout - 0.8 * atr), 2)
        later = [level for level in resistances if level > confirmation]
        target1 = round(later[0] if later else confirmation + 1.5 * atr, 2)
        target2 = round(max(later[-1] if later else target1, confirmation + 3 * atr, target1 + 1.5 * atr), 2)
    midpoint = sum(entry) / 2
    risk = max(0.01, midpoint - stop)
    rr = round((target1 - midpoint) / risk, 2)
    return entry, stop, [target1, target2], rr


def audit(directory):
    files = {name: directory / filename for name, filename in {
        "filings": "filings.json", "financials": "financials.json", "technicals": "technicals.json",
        "macro": "macro.json", "news": "news-catalysts.json",
    }.items()}
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        errors = load_optional(directory / "collection-errors.json", {})
        available = {name: load_optional(path, {}) for name, path in files.items() if path.exists()}
        primary = (available.get("technicals", {}).get("results") or [{}])[0]
        financial_freshness = available.get("financials", {}).get("freshness", {})
        macro_snapshot = available.get("macro", {}).get("market_snapshot", {})
        news_freshness = available.get("news", {}).get("freshness", {})
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "BLOCK_ACTION",
            "action_allowed": False,
            "missing_layers": missing,
            "collection_errors": errors,
            "gates": {},
            "warnings": [f"数据层未完成：{name}；原始错误已记录" for name in missing],
            "as_of": {
                "market_price": nested(primary, "freshness", "data_date"),
                "financial_period": financial_freshness.get("official_period"),
                "financial_filing": financial_freshness.get("official_filing_date"),
                "vix": nested(macro_snapshot, "vix", "last_trade_time"),
                "treasury_yields": nested(macro_snapshot, "treasury_yields", "date"),
                "newest_news": news_freshness.get("newest_news_at"),
            },
        }
    layers = {name: load(path) for name, path in files.items()}
    primary = (layers["technicals"].get("results") or [{}])[0]
    gates = {
        "sec_index_current": layers["filings"].get("freshness", {}).get("status") == "CURRENT_INDEX",
        "applicable_filing_present": bool(layers["filings"].get("quality_gate_passed")),
        "financial_period_current": layers["financials"].get("freshness", {}).get("status") == "CURRENT",
        "current_quote_checked": bool(layers["financials"].get("freshness", {}).get("latest_quote_checked")),
        "market_price_current": bool(primary.get("freshness", {}).get("is_latest_available")),
        "macro_current": layers["macro"].get("freshness", {}).get("status") == "CURRENT",
        "current_vix_present": bool(layers["macro"].get("market_snapshot", {}).get("vix", {}).get("value")),
        "news_within_7_days": layers["news"].get("freshness", {}).get("status") == "CURRENT",
    }
    recent_ipo = layers["financials"].get("selected_latest", {}).get("company_stage") == "recent_ipo"
    normalization = bool(layers["financials"].get("valuation", {}).get("normalization_required")) or recent_ipo
    warnings = layers["filings"].get("warnings", []) + layers["financials"].get("warnings", []) + layers["news"].get("warnings", [])
    if normalization:
        warnings.append("Valuation conclusion is unavailable: normalized TTM earnings are not decision-grade")
    allowed = all(gates.values())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "status": "CURRENT" if allowed else "BLOCK_ACTION",
        "action_allowed": allowed, "valuation_conclusion_allowed": not normalization, "gates": gates,
        "warnings": list(dict.fromkeys(warnings)),
        "as_of": {
            "market_price": primary.get("freshness", {}).get("data_date"),
            "financial_period": layers["financials"].get("freshness", {}).get("official_period"),
            "financial_filing": layers["financials"].get("freshness", {}).get("official_filing_date"),
            "vix": layers["macro"].get("market_snapshot", {}).get("vix", {}).get("last_trade_time"),
            "treasury_yields": layers["macro"].get("market_snapshot", {}).get("treasury_yields", {}).get("date"),
            "newest_news": layers["news"].get("freshness", {}).get("newest_news_at"),
        },
    }


def doctor(output, with_supplemental=False):
    checks = {
        "uv": bool(shutil.which("uv")), "longbridge": bool(shutil.which("longbridge")),
        "fred_api_key": bool(os.environ.get("FRED_API_KEY")),
        "sec_user_agent_with_contact": bool(re.search(r"@|https?://", os.environ.get("SEC_USER_AGENT", ""))),
        "agent_reach": bool(shutil.which("agent-reach")), "opencli_cmd": bool(shutil.which("opencli.cmd")),
        "dependencies": {name: path.exists() for name, path in DEPENDENCIES.items()},
    }
    # Longbridge is an optional accelerator. Public SEC/Yahoo/RSS sources are
    # the zero-account baseline and determine whether the workflow is usable.
    checks["public_source_smoke"] = {}
    try:
        checks["public_source_smoke"]["sec_identity"] = bool(sec_company("SPY"))
    except Exception as exc:
        checks["public_source_smoke"]["sec_identity"] = False
        checks["public_sec_error"] = str(exc)[:1000]
    try:
        checks["public_source_smoke"]["yahoo_quote"] = bool(yahoo_quote("SPY"))
    except Exception as exc:
        checks["public_source_smoke"]["yahoo_quote"] = False
        checks["public_yahoo_error"] = str(exc)[:1000]
    try:
        checks["public_source_smoke"]["google_news_rss"] = bool(rss_news("AAPL", "Apple", 1)[0])
    except Exception as exc:
        checks["public_source_smoke"]["google_news_rss"] = False
        checks["public_news_error"] = str(exc)[:1000]
    if checks["longbridge"]:
        try:
            check_output = execute([shutil.which("longbridge"), "check", "--format", "json"], timeout=60)
            check_payload = json.loads(check_output)
            checks["longbridge_authenticated"] = longbridge_check_authenticated(check_payload)
            if not checks["longbridge_authenticated"]:
                checks["longbridge_error"] = str((check_payload.get("session") or {}).get("detail") or "Longbridge session is not usable")
        except Exception as exc:
            checks["longbridge_authenticated"] = False
            checks["longbridge_error"] = str(exc)
    checks["supplemental_search_smoke"] = {}
    opencli = shutil.which("opencli.cmd") or shutil.which("opencli")
    if with_supplemental and checks["agent_reach"] and opencli:
        for platform in ("twitter", "reddit"):
            try:
                execute([opencli, platform, "search", "SPY US stock market", "-f", "yaml"], timeout=45)
                checks["supplemental_search_smoke"][platform] = True
            except Exception as exc:
                checks["supplemental_search_smoke"][platform] = False
                checks[f"{platform}_search_error"] = str(exc)[:1000]
    public_ready = all(checks["public_source_smoke"].values())
    required = [checks["uv"], checks["fred_api_key"], public_ready, all(checks["dependencies"].values())]
    supplemental_ready = bool(checks["supplemental_search_smoke"]) and all(checks["supplemental_search_smoke"].values())
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready": all(required),
        "full_power": all(required) and checks["sec_user_agent_with_contact"],
        "supplemental_ready": supplemental_ready,
        "longbridge_optional": True,
        "checks": checks,
    }
    dump(output, result)
    print(f"Success! Doctor results written to: {output.resolve()}")
    return result


def collect(code, horizon, output):
    total_started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    errors = {}
    timings = {}
    # Resolve the security before starting expensive independent work. This adds
    # negligible latency for supported tickers and makes unsupported/unknown
    # symbols fail immediately without unnecessary SEC or macro requests.
    company_started = time.perf_counter()
    company, sector = company_and_sector(code)
    timings["security_identity"] = {"status": "ok", "seconds": round(time.perf_counter() - company_started, 3)}
    dump(output / "manifest.json", {"ticker": code, "symbol": f"{code}.US", "horizon": horizon, "generated_at": datetime.now(timezone.utc).isoformat(), "company": company, "sector_etf": sector})
    symbols = technical_symbols(code, sector)
    # SEC is a prerequisite for financial reconciliation and news evidence, while
    # market candles and macro data are independent. Start the independent work
    # immediately, then fan out the filing-dependent jobs once SEC completes.
    with ThreadPoolExecutor(max_workers=5) as pool:
        independent = {
            "macro": pool.submit(timed_execute, py(DEPENDENCIES["macro"], "--output", output / "macro.json")),
            "filings": pool.submit(timed_execute, py(DEPENDENCIES["filings"], "--symbol", f"{code}.US", "--limit", "30", "--output", output / "filings.json")),
        }
        independent["technicals"] = pool.submit(timed_execute, py(DEPENDENCIES["technicals"], symbols, "--period", "1y", "--source", "auto", "--output", output / "technicals.json"))
        timings["filings"] = independent["filings"].result()
        if timings["filings"]["status"] == "error":
            errors["filings"] = timings["filings"]["error"]
        if "filings" not in errors:
            dependent = {
                "financials": pool.submit(timed_execute, py(DEPENDENCIES["financials"], "--symbol", f"{code}.US", "--filings", output / "filings.json", "--output", output / "financials.json")),
                # Headlines, timestamps, sources and links drive the seven-day gate.
                # Article bodies are not consumed by the deterministic analyzer and
                # frequently time out, so the default run avoids that dead latency.
                "news": pool.submit(timed_execute, py(DEPENDENCIES["news"], "--symbol", f"{code}.US", "--filings", output / "filings.json", "--detail-count", "0", "--output", output / "news-catalysts.json")),
            }
            for future in as_completed(dependent.values()):
                name = next(key for key, value in dependent.items() if value is future)
                timings[name] = future.result()
                if timings[name]["status"] == "error":
                    errors[name] = timings[name]["error"]
        for name in ("macro", "technicals"):
            timings[name] = independent[name].result()
            if timings[name]["status"] == "error":
                errors[name] = timings[name]["error"]
    if errors:
        dump(output / "collection-errors.json", errors)
    quality = audit(output)
    dump(output / "quality.json", quality)
    dump(output / "performance.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_seconds": round(time.perf_counter() - total_started, 3),
        "layers": timings,
    })
    print(f"Success! Collection written to: {output.resolve()}")
    return quality


def load_optional(path, default=None):
    if not path.exists():
        return {} if default is None else default
    try:
        return load(path)
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def nested(data, *keys, default=None):
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def display(value, digits=2, suffix=""):
    if value is None or value == "":
        return "N/A"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return str(value)


def human_warning(value):
    text = re.sub(r"\n\s*trace_id:\s*\S+.*$", "", str(value or ""), flags=re.I | re.S)
    replacements = (
        (r"Vendor PE ([\d.]+) uses a stale denominator; recomputed TTM PE is ([\d.]+)", r"供应商 PE（\1）分母已滞后；按最新四季 EPS 重算为 \2"),
        (r"Invalid non-positive industry median PE rejected: (.+)", r"行业 PE 中位数为无效的非正值（\1），已排除"),
        (r"Valuation conclusion is unavailable: normalized TTM earnings are not decision-grade", r"标准化 TTM 盈利尚不具备决策质量，因此不下估值结论"),
        (r"SEC submissions fallback failed: (.+)", r"SEC 官方索引暂时获取失败：\1"),
        (r"SEC Company Facts fallback failed: (.+)", r"SEC Company Facts 备用接口暂时失败：\1"),
        (r"Official filing (.+) overrides lagging structured period (.+)", r"官方文件财务期 \1 覆盖了滞后的结构化财务期 \2"),
        (r"SEC financial filing confirms the period; standardized financial values use period-aligned Longbridge data", r"SEC 财务文件确认了财务期；标准化财务值采用与该期对齐的 Longbridge 数据"),
        (r"Official earnings are reported in native currency; USD/ADR TTM PE recomputation is disabled", r"官方盈利以原币披露；已禁用美元/ADR TTM PE 重算"),
        (r"GAAP EPS contains material non-operating items; normalized PE is required before valuation conclusions", r"GAAP EPS 含重大非经营性项目；下估值结论前必须使用标准化 PE"),
        (r"Missing core official financial fields: (.+)", r"官方财务核心字段缺失：\1"),
        (r"SEC_USER_AGENT is not configured with operator contact information", r"SEC_USER_AGENT 未配置操作者联系信息"),
        (r"FPI filing detail (.+) index (.+) failed: (.+)", r"外国发行人财务文件明细 \1（索引 \2）获取失败：\3"),
        (r"Filing detail index (.+) failed: (.+)", r"财务文件明细索引 \1 获取失败：\2"),
        (r"Agent Reach doctor refresh failed; last successful backend routing cache used", r"Agent Reach 健康检查刷新失败，已使用最近一次成功的路由缓存"),
        (r"Agent Reach returned no active backend; Longbridge/SEC fallback used", r"Agent Reach 没有可用后端，已使用 Longbridge/SEC 备用来源"),
        (r"Agent Reach unavailable; Longbridge/SEC fallback used: (.+)", r"Agent Reach 不可用，已使用 Longbridge/SEC 备用来源：\1"),
        (r"Agent Reach CLI unavailable; Longbridge/SEC fallback used", r"Agent Reach CLI 不可用，已使用 Longbridge/SEC 备用来源"),
        (r"No news item within the required seven-day window", r"最近 7 天内没有满足要求的相关新闻"),
        (r"News detail (.+) unavailable; list metadata retained", r"新闻 \1 正文不可用，已保留列表元数据"),
        (r"Longbridge unavailable; SEC public fallback used: (.+)", r"Longbridge 不可用，已使用 SEC 公共回退：\1"),
        (r"Longbridge (.+) unavailable; public/SEC fallback used: (.+)", r"Longbridge 的 \1 数据不可用，已使用公共/SEC 回退：\2"),
        (r"Longbridge quote unavailable; current price uses Yahoo public chart", r"Longbridge 行情不可用，当前价使用 Yahoo 公共图表"),
        (r"Longbridge news unavailable; Google News RSS metadata used: (.+)", r"Longbridge 新闻不可用，已使用 Google News RSS 元数据：\1"),
        (r"Longbridge corporate actions unavailable; SEC filing catalysts retained: (.+)", r"Longbridge 公司行动不可用，已保留 SEC 文件催化剂：\1"),
        (r"Public quote fallback failed: (.+)", r"公共行情回退失败：\1"),
    )
    for pattern, replacement in replacements:
        if re.fullmatch(pattern, text, re.S):
            return re.sub(pattern, replacement, text, flags=re.S)
    return text


def collapse_provider_warnings(values):
    """Keep Markdown readable while preserving raw provider errors in JSON."""
    cleaned = []
    longbridge_seen = False
    for value in values:
        text = human_warning(value)
        if re.search(r"Longbridge", text, re.I):
            longbridge_seen = True
            continue
        if text and text not in cleaned:
            cleaned.append(text)
    if longbridge_seen:
        cleaned.insert(0, "Longbridge 不可用，本次完整使用 SEC、Yahoo、Google News 等公共回退来源；无需券商开户。")
    return cleaned


def money(value):
    if value is None or value == "":
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    absolute = abs(number)
    if absolute >= 1e9:
        return f"${number / 1e9:,.2f}B"
    if absolute >= 1e6:
        return f"${number / 1e6:,.2f}M"
    return f"${number:,.2f}"


def native_money(value, currency):
    if value is None:
        return "N/A"
    symbols = {"EUR": "\u20ac", "TWD": "NT$"}
    prefix = symbols.get(currency, f"{currency} " if currency else "")
    number = float(value)
    if abs(number) >= 1e9:
        return f"{prefix}{number / 1e9:,.2f}B"
    if abs(number) >= 1e6:
        return f"{prefix}{number / 1e6:,.2f}M"
    return f"{prefix}{number:,.2f}"


def markdown_link(label, url):
    clean_label = str(label or "来源").replace("[", "\\[").replace("]", "\\]")
    return f"[{clean_label}]({url})" if url else clean_label


def standard_report_payload(directory, analysis):
    manifest = load_optional(directory / "manifest.json")
    filings = load_optional(directory / "filings.json")
    financials = load_optional(directory / "financials.json")
    technicals = load_optional(directory / "technicals.json")
    macro_layer = load_optional(directory / "macro.json")
    news_layer = load_optional(directory / "news-catalysts.json")
    quality = analysis.get("quality") or load_optional(directory / "quality.json")
    ticker_code = analysis.get("ticker") or manifest.get("ticker") or directory.parent.name
    horizon = analysis.get("horizon") or manifest.get("horizon") or "2-8w"
    recommendation = analysis.get("recommendation") or "数据不足"
    blocked = not quality.get("action_allowed", False)

    primary = (technicals.get("results") or [{}])[0]
    raw_tech = analysis.get("technical") or {}
    structure = primary.get("price_structure") or {}
    indicators = primary.get("indicators") or {}
    tech = {
        "price": raw_tech.get("price", nested(primary, "price", "current")),
        "daily_change_pct": nested(primary, "price", "change_pct"),
        "rsi14": raw_tech.get("rsi14", nested(indicators, "rsi", "value")),
        "sma20": raw_tech.get("sma20", structure.get("sma20")),
        "sma50": raw_tech.get("sma50", structure.get("sma50")),
        "sma200": raw_tech.get("sma200", structure.get("sma200")),
        "macd_histogram": raw_tech.get("macd_histogram", nested(indicators, "macd", "histogram")),
        "atr": nested(indicators, "atr", "value"),
        "atr_pct": nested(indicators, "atr", "percent"),
        "returns_pct": structure.get("returns_pct") or {},
        "support": structure.get("support") or {},
        "resistance": structure.get("resistance") or {},
        "sector_etf": raw_tech.get("sector_etf") or manifest.get("sector_etf"),
        "relative_strength_20d_pct_points": raw_tech.get("relative_strength_20d_pct_points"),
        "short_history": raw_tech.get("short_history"),
    }

    fundamentals = analysis.get("fundamentals") or financials.get("selected_latest") or {}
    valuation = analysis.get("valuation") or financials.get("valuation") or {}
    macro = analysis.get("macro") or {}
    vix = macro.get("vix", nested(macro_layer, "market_snapshot", "vix", "value"))
    vix_change = macro.get("vix_change_pct", nested(macro_layer, "market_snapshot", "vix", "change_pct"))
    treasury = macro.get("treasury") or nested(macro_layer, "market_snapshot", "treasury_yields", default={}) or {}
    fred_series = {
        item.get("series_id"): {
            "value": item.get("latest_value"),
            "date": item.get("latest_date"),
            "status": nested(item, "freshness", "status"),
            "source": item.get("source"),
        }
        for item in macro_layer.get("series", [])
        if item.get("series_id") and "error" not in item
    }
    expected_ipo_price = fundamentals.get("expected_ipo_price_usd")
    current_price = tech.get("price")
    price_vs_expected_ipo_pct = None
    if expected_ipo_price and current_price is not None:
        price_vs_expected_ipo_pct = round((float(current_price) / float(expected_ipo_price) - 1) * 100, 2)
    recent_ipo_observation = None
    if fundamentals.get("company_stage") == "recent_ipo":
        recent_ipo_observation = {
            "company_stage": "recent_ipo",
            "expected_ipo_price_usd": expected_ipo_price,
            "expected_ipo_price_status": fundamentals.get("expected_ipo_price_status"),
            "current_price_vs_expected_ipo_pct": price_vs_expected_ipo_pct,
            "listing_date": None,
            "days_since_listing": None,
            "lockup_expiry": None,
            "short_history": bool(tech.get("short_history")),
        }

    relevant_news = [item for item in news_layer.get("news", []) if item.get("relevant")]
    news_items = [
        {
            "published_at": item.get("published_at"),
            "title": item.get("title"),
            "url": item.get("url"),
            "detail_status": item.get("detail_status", "metadata_only"),
        }
        for item in relevant_news[:5]
    ]
    catalysts = analysis.get("catalysts") or news_layer.get("catalysts") or {}

    no_new_money = recommendation in {"回避", "卖出", "减仓"}
    if blocked:
        action_plan = {
            "entry_or_confirmation": None,
            "invalidation_stop": None,
            "targets": [],
            "risk_reward_to_target1": None,
            "current_entry_risk_reward_to_target1": None,
            "confirmation_risk_reward_to_target1": None,
            "note": "质量闸门未通过，禁止生成买卖价位。",
        }
    elif no_new_money:
        action_plan = {
            "entry_or_confirmation": None,
            "invalidation_stop": None,
            "targets": [],
            "risk_reward_to_target1": None,
            "current_entry_risk_reward_to_target1": None,
            "confirmation_risk_reward_to_target1": None,
            "note": "当前结论不支持新资金介入；等待趋势、相对强弱和波动率共同改善后重新评估。",
        }
    else:
        action_plan = {
            "entry_or_confirmation": analysis.get("entry_or_confirmation"),
            "invalidation_stop": analysis.get("invalidation_stop"),
            "targets": analysis.get("targets") or [],
            "risk_reward_to_target1": analysis.get("risk_reward_to_target1"),
            "current_entry_risk_reward_to_target1": analysis.get("current_entry_risk_reward_to_target1"),
            "confirmation_risk_reward_to_target1": analysis.get("confirmation_risk_reward_to_target1"),
            "note": "价格仅在确认条件满足后有效。",
        }

    failed_gates = [name for name, passed in quality.get("gates", {}).items() if not passed]
    warnings = list(quality.get("warnings", []))
    for layer, error in (quality.get("collection_errors") or {}).items():
        warnings.append(f"{layer} 数据源失败：{str(error).splitlines()[-1][:300]}")
    if quality.get("missing_layers"):
        warnings.insert(0, f"缺失核心数据层：{', '.join(quality['missing_layers'])}")
    if failed_gates:
        warnings.insert(0, f"未通过质量门槛：{', '.join(failed_gates)}")
    warnings = collapse_provider_warnings(warnings)

    sources = []
    for label, url in (
        ("SEC submissions", filings.get("submissions_url")),
        ("SEC 财务文件", financials.get("source_url")),
        ("SEC Company Facts", financials.get("company_facts_url")),
        ("Cboe VIX", nested(macro_layer, "market_snapshot", "vix", "source_url")),
        ("美国财政部收益率曲线", treasury.get("source_url")),
        # Yahoo's public chart endpoint uses the native ticker (AAPL), not the
        # internal Longbridge-style AAPL.US symbol used elsewhere in the run.
        ("Yahoo Chart 公共行情", YAHOO_CHART_URL.format(symbol=ticker_code.replace(".", "-"))),
        ("Google News RSS 公共新闻", news_layer.get("public_news_url")),
    ):
        if url and not any(item["url"] == url for item in sources):
            sources.append({"label": label, "url": url})
    for item in news_items:
        if item.get("url"):
            sources.append({"label": item.get("title") or "相关新闻", "url": item["url"]})

    if blocked:
        scenarios = {
            "bull": "核心数据恢复并通过全部质量门槛后，才评估上行情景。",
            "base": "维持数据不足状态，不生成方向性结论。",
            "bear": "数据缺失或过期持续存在，任何价格判断都可能失真。",
        }
    else:
        entry = action_plan["entry_or_confirmation"] or []
        targets = action_plan["targets"]
        confirmation = display(entry[0]) if entry else "确认位"
        target2 = display(targets[-1]) if targets else "第二目标"
        scenarios = {
            "bull": f"价格满足 {confirmation} 附近确认条件并延续相对强势，观察 {target2}。",
            "base": "价格在支撑与阻力之间震荡，等待盈利、估值或催化剂进一步确认。",
            "bear": f"价格跌破 {display(action_plan['invalidation_stop'])}，当前交易逻辑失效。",
        }

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker_code,
        "company": manifest.get("company") or {},
        "horizon": horizon,
        "status": quality.get("status", "BLOCK_ACTION"),
        "action_allowed": quality.get("action_allowed", False),
        "recommendation": recommendation,
        "confidence": analysis.get("confidence", "低" if blocked else "中"),
        "reason": analysis.get("reason") or ("核心数据未通过最新可用值门槛。" if blocked else "由基本面、技术面、宏观和事件层综合得出。"),
        "action_plan": action_plan,
        "decision_model": {
            "action_drivers": ["技术信号", "Cboe VIX", "第一目标风险收益比"],
            "hard_quality_gates": ["SEC/官方财务新鲜度", "最新行情", "宏观数据", "最近新闻"],
            "risk_modifiers": ["基本面", "估值可用性", "官方事件与新闻"],
            "supplemental_only": ["X", "Reddit"],
            "equal_weighted": False,
        },
        "recent_ipo_observation": recent_ipo_observation,
        "fundamentals": fundamentals,
        "valuation": valuation,
        "valuation_conclusion_allowed": analysis.get("valuation_conclusion_allowed", quality.get("valuation_conclusion_allowed", False)),
        "technicals": tech,
        "macro": {
            "vix": vix,
            "vix_change_pct": vix_change,
            "treasury": treasury,
            "fred_series": fred_series,
        },
        "news": news_items,
        "social_cross_check": {
            "available": nested(news_layer, "agent_reach", "available", default=False),
            "verified": nested(news_layer, "agent_reach", "verified", default=False),
        },
        "catalysts": {"confirmed": catalysts.get("confirmed", []), "estimated": catalysts.get("estimated", [])},
        "scenarios": scenarios,
        "risks_and_limitations": warnings,
        "freshness": quality.get("as_of", {}),
        "gates": quality.get("gates", {}),
        "sources": sources,
    }


def unavailable_report(code, horizon, output, error, category="UNRECOGNIZED_SYMBOL"):
    """Persist a safe, explicit result for unsupported, unknown, or temporary failures."""
    output.mkdir(parents=True, exist_ok=True)
    if category == "UNSUPPORTED_VENUE":
        message = f"{code} 可能是有效证券，但不在当前美国交易所/ADR覆盖范围：{error}"
        reason = "证券的主上市地或代码不在当前数据源覆盖范围，未擅自映射到其他股票。"
    elif category == "PROVIDER_TEMPORARY_ERROR":
        message = f"数据源暂时不可用，无法确认 {code}.US 的最新数据：{error}"
        reason = "行情或身份数据源临时失败；这不是“代码不存在”，本次不做方向判断。"
    else:
        message = f"{code}.US 未被配置的数据源识别为可交易的美股或 ADR：{error}"
        reason = "未能确认其为可交易的美股或 ADR，不能安全生成分析结论。"
    quality = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCK_ACTION",
        "action_allowed": False,
        "valuation_conclusion_allowed": False,
        "gates": {"symbol_recognized": False},
        "failure_category": category,
        "warnings": [message],
        "as_of": {},
    }
    analysis = {
        "ticker": code,
        "horizon": horizon,
        "recommendation": "数据不足",
        "reason": reason,
        "quality": quality,
    }
    dump(output / "manifest.json", {"ticker": code, "symbol": f"{code}.US", "horizon": horizon, "status": "UNRECOGNIZED"})
    dump(output / "quality.json", quality)
    dump(output / "analysis.json", analysis)
    report(output)
    print(f"Safe report ({category}) written to: {output.resolve()}")


def analyze(directory):
    quality = load(directory / "quality.json") if (directory / "quality.json").exists() else audit(directory)
    if not quality.get("action_allowed"):
        result = {"recommendation": "数据不足", "reason": "核心数据未通过最新可用值门槛", "horizon": "2-8w", "quality": quality}
        dump(directory / "analysis.json", result)
        return result
    manifest, financials, technicals, macro, news = [load(directory / name) for name in ("manifest.json", "financials.json", "technicals.json", "macro.json", "news-catalysts.json")]
    primary = technicals["results"][0]
    price = float(primary["price"]["current"])
    structure = primary["price_structure"]
    indicators = primary["indicators"]
    atr = float(indicators["atr"]["value"])
    rsi = float(indicators["rsi"]["value"])
    def optional_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    sma20, sma50, sma200 = (optional_float(structure.get(key)) for key in ("sma20", "sma50", "sma200"))
    histogram = optional_float((indicators.get("macd") or {}).get("histogram"))
    sector = manifest.get("sector_etf")
    relative20 = float(primary.get("relative_strength_pct_points", {}).get(sector, {}).get("20", 0))
    signals = [price > sma20 if sma20 is not None else None, sma20 > sma50 if sma20 is not None and sma50 is not None else None, price > sma200 if sma200 is not None else None, 42 <= rsi <= 68, histogram > 0 if histogram is not None else None, relative20 > 0]
    score = sum(signal is True for signal in signals)
    short_history = any(value is None for value in (sma50, sma200, histogram))
    vix = float(macro["market_snapshot"]["vix"]["value"])
    if vix >= 25:
        score -= 2
    elif vix >= 20:
        score -= 1
    supports = sorted({float(v) for v in structure["support"].values() if float(v) < price}, reverse=True)
    resistances = sorted({float(v) for v in structure["resistance"].values() if float(v) > price})
    support = supports[0] if supports else price - 2 * atr
    provisional_entry, provisional_stop, provisional_targets, provisional_rr = trade_levels(
        "买入", price, atr, support, resistances
    )
    if short_history and ((sma20 is not None and price < sma20) or float(structure.get("returns_pct", {}).get("20", 0)) <= -20):
        recommendation = "回避"
    elif short_history:
        recommendation = "等待"
    elif score >= 5 and provisional_rr >= 1.5:
        recommendation = "买入"
    elif score >= 4 and provisional_rr >= 1.5:
        recommendation = "分批买入"
    elif score <= 1:
        recommendation = "回避"
    else:
        recommendation = "等待"
    entry, stop, targets, rr = trade_levels(recommendation, price, atr, support, resistances)
    cash_flow_evidence = (financials.get("selected_latest") or {}).get("evidence", {})
    cash_flow_days = max(
        int((cash_flow_evidence.get(field) or {}).get("duration_days") or 0)
        for field in ("operating_cash_flow_usd", "capex_usd")
    )
    cash_flow_basis = "半年累计" if cash_flow_days > 120 else "季度"
    period = (financials.get("selected_latest") or {}).get("period") or "最新财务期未确认"
    tech_state = []
    if sma20 is not None:
        tech_state.append("股价在SMA20上方" if price > sma20 else "股价在SMA20下方")
    if sma200 is not None:
        tech_state.append("仍在SMA200上方" if price > sma200 else "跌破SMA200")
    if histogram is not None:
        tech_state.append("MACD柱为正" if histogram > 0 else "MACD柱为负")
    tech_state.append(f"20日相对{sector} {'强' if relative20 > 0 else '弱'}{abs(relative20):.1f}个百分点")
    vix_state = f"VIX {vix:.2f}"
    reason_parts = [
        f"技术面：{'、'.join(tech_state[:3])}",
        f"宏观：{vix_state}{'，风险偏高' if vix >= 20 else '，处于常态区间'}",
        f"基本面：官方/核验财务期 {period}",
    ]
    warning_summary = collapse_provider_warnings(quality.get("warnings", []))
    material_warnings = [item for item in warning_summary if not item.startswith("Longbridge 不可用，本次完整使用")]
    if material_warnings:
        reason_parts.append(f"限制：{material_warnings[0]}")
    if recommendation == "等待":
        reason_parts.append(f"动作依据：有效技术信号 {score}/6；当前价入场风险收益比 {provisional_rr:.2f}，确认突破后风险收益比 {rr:.2f}；尚未同时满足买入门槛（至少4个信号且当前价入场风险收益比不低于1.50）")
    elif recommendation in {"买入", "分批买入"}:
        reason_parts.append(f"动作依据：有效技术信号 {score}/6，第一目标风险收益比 {provisional_rr:.2f}，达到{recommendation}门槛")
    elif recommendation == "回避":
        reason_parts.append(f"动作依据：有效技术信号仅 {score}/6，趋势或短历史风险不支持新资金介入")
    reason = "；".join(reason_parts) + "。"
    result = {
        "ticker": manifest["ticker"], "horizon": manifest["horizon"], "recommendation": recommendation,
        "reason": reason,
        "confidence": "低" if short_history else ("中" if material_warnings else "高"), "score": score,
        "entry_or_confirmation": entry, "invalidation_stop": stop,
        "targets": targets, "risk_reward_to_target1": rr,
        "current_entry_risk_reward_to_target1": provisional_rr,
        "confirmation_risk_reward_to_target1": rr,
        "technical": {"price": price, "rsi14": rsi, "sma20": sma20, "sma50": sma50, "sma200": sma200, "macd_histogram": histogram, "short_history": short_history, "sector_etf": sector, "relative_strength_20d_pct_points": relative20},
        "macro": {"vix": vix, "vix_change_pct": macro["market_snapshot"]["vix"].get("change_pct"), "treasury": macro["market_snapshot"].get("treasury_yields")},
        "fundamentals": {**(financials.get("selected_latest") or {}), "cash_flow_basis": cash_flow_basis, "cash_flow_duration_days": cash_flow_days}, "valuation": financials.get("valuation"),
        "valuation_conclusion_allowed": quality.get("valuation_conclusion_allowed"),
        "catalysts": news.get("catalysts"), "quality": quality,
    }
    dump(directory / "analysis.json", result)
    print(f"Success! Analysis written to: {(directory / 'analysis.json').resolve()}")
    return result


def report(directory):
    analysis = load(directory / "analysis.json")
    payload = standard_report_payload(directory, analysis)
    plan = payload["action_plan"]
    tech = payload["technicals"]
    fundamentals = payload["fundamentals"]
    valuation = payload["valuation"]
    macro = payload["macro"]

    company_name = payload["company"].get("company_name") or payload["company"].get("name")
    title = f"{company_name}（{payload['ticker']}.US）" if company_name else f"{payload['ticker']}.US"
    entry = plan.get("entry_or_confirmation") or []
    targets = plan.get("targets") or []
    action_rows = [
        ("当前动作", payload["recommendation"] if payload["action_allowed"] else "数据不足，禁止行动建议"),
        ("入场/确认条件", f"${display(entry[0])} - ${display(entry[1])}" if len(entry) >= 2 else "N/A"),
        ("失效/止损依据", f"${display(plan.get('invalidation_stop'))}" if plan.get("invalidation_stop") is not None else "N/A"),
        ("第一目标", f"${display(targets[0])}" if targets else "N/A"),
        ("第二目标", f"${display(targets[1])}" if len(targets) > 1 else "N/A"),
        ("当前价入场风险收益比", display(plan.get("current_entry_risk_reward_to_target1"))),
        ("确认突破风险收益比", display(plan.get("confirmation_risk_reward_to_target1"))),
        ("持有周期", payload["horizon"]),
    ]
    action_table = "\n".join(f"| {label} | {value} |" for label, value in action_rows)

    news_lines = []
    for item in payload["news"]:
        date = (item.get("published_at") or "N/A")[:10]
        status = "正文可用" if item.get("detail_status") == "available" else "仅元数据"
        news_lines.append(f"- {date}：{markdown_link(item.get('title'), item.get('url'))}（{status}）")
    if not news_lines:
        news_lines = ["- 未取得最近 7 天内可验证的相关报道。"]

    catalyst_rows = []
    for status, label in (("confirmed", "已确认"), ("estimated", "预计/待确认")):
        for item in payload["catalysts"].get(status, []):
            if isinstance(item, dict):
                date = item.get("date") or item.get("start") or "日期未定"
                event = item.get("event") or item.get("title") or item.get("name") or str(item)
            else:
                date, event = "日期未定", str(item)
            catalyst_rows.append(f"| {date} | {event} | {label} |")
    if not catalyst_rows:
        catalyst_rows = ["| N/A | 当前数据未确认未来 8 周事件 | 未确认 |"]

    warning_lines = [f"- {item}" for item in payload["risks_and_limitations"]]
    if not warning_lines:
        warning_lines = ["- 未发现额外的数据质量警告。"]

    freshness_rows = [
        ("股价/K 线", payload["freshness"].get("market_price"), "CURRENT" if payload["gates"].get("market_price_current") else "未通过"),
        ("SEC 官方索引", payload["freshness"].get("financial_filing"), "CURRENT" if payload["gates"].get("sec_index_current") else "未通过"),
        ("最新财务期", payload["freshness"].get("financial_period"), "CURRENT" if payload["gates"].get("financial_period_current") else "未通过"),
        ("当前 VIX", payload["freshness"].get("vix"), "CURRENT" if payload["gates"].get("current_vix_present") else "未通过"),
        ("美债收益率", payload["freshness"].get("treasury_yields"), "CURRENT" if macro.get("treasury") else "未通过"),
        ("最近新闻", payload["freshness"].get("newest_news"), "CURRENT" if payload["gates"].get("news_within_7_days") else "未通过"),
    ]
    freshness_table = "\n".join(
        f"| {layer} | {value or 'N/A'} | {status} |" for layer, value, status in freshness_rows
    )

    source_lines = [f"- {markdown_link(item['label'], item['url'])}" for item in payload["sources"]]
    if not source_lines:
        source_lines = ["- 本次没有取得可点击的外部来源 URL；请以运行目录中的原始 JSON 为准。"]

    valuation = payload.get("valuation") or {}
    valuation_available = any(
        valuation.get(field) is not None
        for field in ("vendor_pe_ttm", "recomputed_pe_ttm", "industry_median_pe")
    )
    if not payload["valuation_conclusion_allowed"]:
        valuation_note = "不允许；仅展示原始估值字段，不据此下结论"
    elif not valuation_available:
        valuation_note = "未取得可用 PE 数据，未下估值结论"
    else:
        valuation_note = "允许"
    native_currency = fundamentals.get("native_currency")
    revenue_display = native_money(fundamentals.get("revenue_native"), native_currency) if native_currency and fundamentals.get("revenue_native") is not None else money(fundamentals.get("revenue_usd"))
    net_income_display = native_money(fundamentals.get("net_income_native"), native_currency) if native_currency and fundamentals.get("net_income_native") is not None else money(fundamentals.get("net_income_usd"))
    eps_display = display(fundamentals.get("diluted_eps_native")) + f" {native_currency}" if native_currency and fundamentals.get("diluted_eps_native") is not None else display(fundamentals.get("diluted_eps_usd"))
    social_note = "已完成" if payload["social_cross_check"].get("verified") else "未完成或不可验证"
    fred = macro.get("fred_series") or {}
    macro_context_rows = [
        ("联邦基金利率", "FEDFUNDS", "%"),
        ("失业率", "UNRATE", "%"),
        ("10Y-2Y 收益率曲线", "T10Y2Y", " 个百分点"),
        ("美国高收益债利差", "BAMLH0A0HYM2", " 个百分点"),
    ]
    macro_context_table = "\n".join(
        f"| {label} | {display(fred.get(series_id, {}).get('value'), suffix=suffix)} | "
        f"{fred.get(series_id, {}).get('date') or 'N/A'} | {fred.get(series_id, {}).get('status') or 'N/A'} |"
        for label, series_id, suffix in macro_context_rows
    )
    ipo = payload.get("recent_ipo_observation")
    ipo_section = ""
    if ipo:
        expected_price = money(ipo.get("expected_ipo_price_usd"))
        relative_price = display(ipo.get("current_price_vs_expected_ipo_pct"), suffix="%")
        ipo_section = f"""
## 新股观察

| 项目 | 状态 |
|---|---:|
| S-1/A 预期 IPO 价 | {expected_price}（预期值，不是最终定价） |
| 当前价相对预期 IPO 价 | {relative_price} |
| 官方确认上市日期 / 上市天数 | 未确认 / N/A |
| 官方确认锁定期到期日 | 未确认 |
| 技术历史 | {'较短；长期均线或 MACD 可能不可用' if ipo.get('short_history') else '足够'} |
"""
    body = f"""# {title} 标准化股票分析报告

生成时间：{payload['generated_at']}  
默认周期：{payload['horizon']}  
质量状态：`{payload['status']}`  
行动建议许可：{'允许' if payload['action_allowed'] else '禁止'}

## 直接建议

**{payload['recommendation']}**，置信度：{payload['confidence']}。

{payload['reason']}

## 执行条件

| 项目 | 条件 |
|---|---|
{action_table}

说明：{plan['note']}

## 决策模型

- 2–8 周动作由技术信号、Cboe VIX 和第一目标风险收益比直接决定。
- SEC/官方财务、最新行情、宏观与新闻新鲜度是硬质量门；任一核心门失败就禁止行动建议。
- 基本面、估值可用性和官方事件用于风险修正与论据约束，不与技术面等权打分。
- X / Reddit 仅作补充线索，不进入核心评分，也不能替代事实来源。
{ipo_section}

## 基本面

| 指标 | 数值 |
|---|---:|
| 最新财务期 | {fundamentals.get('period') or 'N/A'} |
| 数据主源 | {fundamentals.get('source') or 'N/A'} |
| 收入 | {revenue_display} |
| 经营利润 | {money(fundamentals.get('operating_income_usd'))} |
| 净利润 | {net_income_display} |
| 稀释 EPS / ADR EPS | {eps_display} |
| 经营现金流（{fundamentals.get('cash_flow_basis') or '口径未标注'}） | {money(fundamentals.get('operating_cash_flow_usd'))} |
| 资本开支（{fundamentals.get('cash_flow_basis') or '口径未标注'}） | {money(fundamentals.get('capex_usd'))} |
| 自由现金流（{fundamentals.get('cash_flow_basis') or '口径未标注'}） | {money(fundamentals.get('free_cash_flow_usd'))} |

## 估值

估值结论：**{valuation_note}**。

| 指标 | 数值 |
|---|---:|
| 当前价 | {money(valuation.get('price') or tech.get('price'))} |
| 供应商 PE TTM | {display(valuation.get('vendor_pe_ttm'))} |
| 重算 TTM EPS | {display(valuation.get('recomputed_ttm_eps_usd'))} |
| 重算 PE TTM | {display(valuation.get('recomputed_pe_ttm'))} |
| 行业 PE 中位数 | {display(valuation.get('industry_median_pe'))} |

## 技术面

| 指标 | 数值 |
|---|---:|
| 最新收盘 | {money(tech.get('price'))} |
| 当日涨跌 | {display(tech.get('daily_change_pct'), suffix='%')} |
| RSI14 | {display(tech.get('rsi14'))} |
| SMA20 / SMA50 / SMA200 | {display(tech.get('sma20'))} / {display(tech.get('sma50'))} / {display(tech.get('sma200'))} |
| MACD 柱 | {display(tech.get('macd_histogram'), digits=4)} |
| ATR / ATR% | {display(tech.get('atr'))} / {display(tech.get('atr_pct'), suffix='%')} |
| 5 / 20 / 40 日收益 | {display(tech.get('returns_pct', {}).get('5'), suffix='%')} / {display(tech.get('returns_pct', {}).get('20'), suffix='%')} / {display(tech.get('returns_pct', {}).get('40'), suffix='%')} |
| 相对 {tech.get('sector_etf') or '行业 ETF'} 20 日强弱 | {display(tech.get('relative_strength_20d_pct_points'), suffix=' 个百分点')} |

## 宏观与市场风险

- Cboe 当前 VIX：{display(macro.get('vix'))}，当日变化 {display(macro.get('vix_change_pct'), suffix='%')}。
- 美国 2Y / 10Y 国债收益率：{display(macro.get('treasury', {}).get('us2y_pct'), suffix='%')} / {display(macro.get('treasury', {}).get('us10y_pct'), suffix='%')}。
- X / Reddit 交叉核对：{social_note}；社交内容仅作线索，不作为事实依据。

| 宏观背景 | 最新值 | 数据期 | 状态 |
|---|---:|---|---|
{macro_context_table}

说明：月度指标显示官方最新已发布月份，不以自然日距离误判为过期；这些指标用于风险背景，不直接进入技术评分。

## 最近 7 天新闻

{chr(10).join(news_lines)}

## 未来 8 周催化剂

| 日期 | 事件 | 状态 |
|---|---|---|
{chr(10).join(catalyst_rows)}

## Bull / Base / Bear

- **Bull：** {payload['scenarios']['bull']}
- **Base：** {payload['scenarios']['base']}
- **Bear：** {payload['scenarios']['bear']}

## 风险与数据限制

{chr(10).join(warning_lines)}

## 数据新鲜度

| 数据层 | 截止时间 | 状态 |
|---|---|---|
{freshness_table}

## 来源

{chr(10).join(source_lines)}

以上为标准化研究报告，不构成个性化投资建议；不包含仓位比例，也不会执行交易。
"""
    (directory / "report.md").write_text(body, encoding="utf-8")
    dump(directory / "report.json", {**analysis, "standard_report": payload})
    print(f"Success! Report written to: {(directory / 'report.md').resolve()}")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("doctor"); p.add_argument("--output", required=True); p.add_argument("--with-supplemental", action="store_true")
    for name in ("collect", "run"):
        p = commands.add_parser(name); p.add_argument("--ticker", required=True); p.add_argument("--horizon", default="2-8w"); p.add_argument("--output", required=True)
    for name in ("analyze", "report"):
        p = commands.add_parser(name); p.add_argument("--input", required=True); p.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            doctor(Path(args.output), args.with_supplemental)
        elif args.command == "collect":
            collect(ticker(args.ticker), args.horizon, Path(args.output))
        elif args.command == "analyze":
            analyze(Path(args.input))
        elif args.command == "report":
            report(Path(args.input))
        else:
            directory = Path(args.output)
            try:
                collect(ticker(args.ticker), args.horizon, directory)
            except UnsupportedVenueError as exc:
                unavailable_report(ticker(args.ticker), args.horizon, directory, str(exc), "UNSUPPORTED_VENUE")
                return 0
            except TemporarySourceError as exc:
                unavailable_report(ticker(args.ticker), args.horizon, directory, str(exc), "PROVIDER_TEMPORARY_ERROR")
                return 0
            except UnrecognizedSymbolError as exc:
                unavailable_report(ticker(args.ticker), args.horizon, directory, str(exc), "UNRECOGNIZED_SYMBOL")
                return 0
            except Exception as exc:
                # Identity was either resolved or raised one of the typed errors
                # above. Never reinterpret a later workflow failure as a bad ticker.
                unavailable_report(ticker(args.ticker), args.horizon, directory, str(exc), "PROVIDER_TEMPORARY_ERROR")
                return 0
            analyze(directory)
            report(directory)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
