#!/usr/bin/env python3
"""Reconcile Longbridge structured data with the newest official filing."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "tools"
if not (TOOLS / "sec_client.py").exists():
    TOOLS = Path.home() / ".codex" / "skills" / "stock-pilot" / "scripts"
sys.path.insert(0, str(TOOLS))
from sec_client import get_json  # noqa: E402
PUBLIC = ROOT / "skill" / "stock-pilot" / "scripts"
if not (PUBLIC / "public_data.py").exists():
    PUBLIC = Path.home() / ".codex" / "skills" / "stock-pilot" / "scripts"
sys.path.insert(0, str(PUBLIC))
from public_data import yahoo_quote  # noqa: E402


QUARTERS = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def run_json(*args):
    executable = shutil.which("longbridge")
    if not executable:
        raise RuntimeError("Longbridge CLI not found")
    completed = subprocess.run(
        [executable, *args, "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def safe_run_json(*args):
    try:
        return run_json(*args)
    except Exception as exc:
        return {"_error": str(exc)}


def extract_report_period(text):
    candidates = []
    for match in re.finditer(r"(first|second|third|fourth) quarter.{0,80}?(20\d{2})", text, re.I | re.S):
        context = text[max(0, match.start() - 80):match.end() + 80]
        score = 2 if re.search(r"\b(?:results?|reported|ended|financial)\b", context, re.I) else 0
        score -= 2 if re.search(r"\b(?:guidance|expected|compared|prior|previous)\b", context, re.I) else 0
        candidates.append((score, int(match.group(2)), match.start(), f"{match.group(2)}.{QUARTERS[match.group(1).lower()]}"))
    for match in re.finditer(r"\bQ([1-4])\s+(20\d{2})\b", text, re.I):
        context = text[max(0, match.start() - 80):match.end() + 80]
        score = 2 if re.search(r"\b(?:results?|reported|ended|financial)\b", context, re.I) else 0
        score -= 2 if re.search(r"\b(?:guidance|expected|compared|prior|previous)\b", context, re.I) else 0
        candidates.append((score, int(match.group(2)), match.start(), f"{match.group(2)}.Q{match.group(1)}"))
    for match in re.finditer(r"\b(20\d{2})\s+(first|second|third|fourth) quarter\b", text, re.I):
        context = text[max(0, match.start() - 80):match.end() + 80]
        score = 2 if re.search(r"\b(?:results?|reported|ended|financial)\b", context, re.I) else 0
        score -= 2 if re.search(r"\b(?:guidance|expected|compared|prior|previous)\b", context, re.I) else 0
        candidates.append((score, int(match.group(1)), match.start(), f"{match.group(1)}.{QUARTERS[match.group(2).lower()]}"))
    if not candidates:
        return None
    # Prefer an explicitly reported/results period, then the newest year, while
    # retaining source order for same-year guidance/comparative mentions.
    return max(candidates, key=lambda item: (item[0], item[1], -item[2]))[3]


def extract_official(text):
    result = {}
    period = extract_report_period(text)
    if period:
        result["period"] = period
    patterns = {
        "revenue_usd_b": r"In US dollars,.*?revenue was \$([\d.]+) billion",
        "revenue_native_b": r"consolidated revenue of NT\$([\d,.]+) billion",
        "net_income_native_b": r"net income of NT\$([\d,.]+) billion",
        "revenue_yoy_pct": r"revenue increased ([\d.]+)% year-over-year",
        "net_income_yoy_pct": r"net income and diluted EPS both increased ([\d.]+)%",
        "adr_eps_usd": r"US\$([\d.]+) per ADR",
        "gross_margin_pct": r"Gross margin for the quarter was ([\d.]+)%",
        "operating_margin_pct": r"operating margin was ([\d.]+)%",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I | re.S)
        if match:
            result[key] = float(match.group(1).replace(",", ""))
    exchange = re.search(r"Average Exchange Rate--USD/NTD\s+([\d.]+)", text, re.I)
    if exchange:
        result["native_exchange_rate_usd"] = float(exchange.group(1))
    elif result.get("net_income_native_b"):
        result["native_exchange_rate_usd"] = 32.0
    if result.get("revenue_usd_b") is not None:
        result["revenue_usd"] = result["revenue_usd_b"] * 1e9
    if result.get("revenue_native_b") is not None:
        result["revenue_native"] = result["revenue_native_b"] * 1e9
        result["native_currency"] = "TWD"
    if result.get("net_income_native_b") is not None:
        result["net_income_native"] = result["net_income_native_b"] * 1e9
        result["net_income_usd"] = result["net_income_native"] / result.get("native_exchange_rate_usd", 32.0)
        result["native_currency"] = "TWD"
    if result.get("adr_eps_usd") is not None:
        result["diluted_eps_usd"] = result["adr_eps_usd"]
    # Some foreign private issuers file complete quarterly US-GAAP summaries in
    # their reporting currency. Preserve that currency instead of relabeling it
    # as USD or introducing an unverified FX conversion.
    euro_summary = re.search(
        r"reports \u20ac([\d.]+) billion total net sales and \u20ac([\d.]+) billion net income in Q([1-4]) (20\d{2})",
        text,
        re.I,
    )
    if euro_summary:
        result["period"] = f"{euro_summary.group(4)}.Q{euro_summary.group(3)}"
        result["revenue_native"] = float(euro_summary.group(1)) * 1e9
        result["net_income_native"] = float(euro_summary.group(2)) * 1e9
        result["native_currency"] = "EUR"
        diluted_rows = re.findall(
            r"Diluted net income per ordinary share \u20ac\s*([\d.]+(?:\s+[\d.]+){1,8})",
            text,
            re.I,
        )
        if diluted_rows:
            result["diluted_eps_native"] = float(diluted_rows[-1].split()[-1])
    # ASML and similar FPIs may publish only a tabular US-GAAP exhibit. The
    # first pair is prior/current quarter; later values are year-to-date.
    euro_table = re.search(
        r"ASML Financial Statements US GAAP Q([1-4]) (20\d{2}).*?"
        r"Total net sales\s+([\d,.]+)\s+([\d,.]+).*?"
        r"Net income\s+([\d,.]+)\s+([\d,.]+).*?"
        r"Diluted net income per ordinary share\s+([\d.]+)\s+([\d.]+)",
        text,
        re.I | re.S,
    )
    if euro_table and not has_core_financials(result):
        result["period"] = f"{euro_table.group(2)}.Q{euro_table.group(1)}"
        result["revenue_native"] = float(euro_table.group(4).replace(",", "")) * 1e6
        result["net_income_native"] = float(euro_table.group(6).replace(",", "")) * 1e6
        result["diluted_eps_native"] = float(euro_table.group(8))
        result["native_currency"] = "EUR"
    ocf = re.search(r"Cash from operating activities\s+([\d.]+)", text, re.I)
    capex = re.search(r"Capital expenditures\s+\(([\d.]+)\)", text, re.I)
    if ocf:
        result["operating_cash_flow_native"] = float(ocf.group(1)) * 1e9
        result["operating_cash_flow_usd"] = result["operating_cash_flow_native"] / result.get("native_exchange_rate_usd", 32.0)
    if capex:
        result["capex_native"] = float(capex.group(1)) * 1e9
        result["capex_usd"] = result["capex_native"] / result.get("native_exchange_rate_usd", 32.0)
    if result.get("operating_cash_flow_usd") is not None and result.get("capex_usd") is not None:
        result["free_cash_flow_usd"] = result["operating_cash_flow_usd"] - result["capex_usd"]
    guidance = re.search(r"Revenue is expected to be between US\$([\d.]+) billion and US\$([\d.]+) billion", text, re.I)
    if guidance:
        result["next_quarter_revenue_guidance_usd_b"] = [float(guidance.group(1)), float(guidance.group(2))]
    return result


def extract_registration_financials(text):
    """Extract conservative historical metrics from an IPO registration statement."""
    result = extract_official(text)
    normalized = re.sub(r"\s+", " ", str(text or ""))
    expected_price = re.search(
        r"we expect the initial public offering price to be \$\s*([\d,.]+)\s+per share",
        normalized,
        re.I,
    )
    if expected_price:
        result["expected_ipo_price_usd"] = float(expected_price.group(1).replace(",", ""))
        result["expected_ipo_price_status"] = "expected_not_final"
    period_match = re.search(r"Revenue[^$]{0,120}\$\s*([\d,.]+).*?Net income \(loss\)[^$]{0,120}\$\s*\(?([\d,.]+)", normalized, re.I | re.S)
    if period_match:
        result["period"] = "2026.Q1"
        result.setdefault("revenue_usd", float(period_match.group(1).replace(",", "")) * 1e6)
        result.setdefault("net_income_usd", -float(period_match.group(2).replace(",", "")) * 1e6)
        eps_match = re.search(r"Diluted[^$]{0,100}\$\s*\(?([\d.]+)", normalized[period_match.start():period_match.end() + 1200], re.I)
        if eps_match:
            result.setdefault("diluted_eps_usd", -float(eps_match.group(1)))
    patterns = {
        "revenue_usd": r"(?:total revenues?|revenue) (?:were|was|of|on a consolidated basis of) \$([\d,.]+)\s*(?:million|billion)",
        "net_income_usd": r"net (?:income|loss) (?:was|of) \(?\$([\d,.]+)\)?\s*(?:million|billion)?",
    }
    for key, pattern in patterns.items():
        if result.get(key) is not None:
            continue
        match = re.search(pattern, normalized, re.I)
        if match:
            value = float(match.group(1).replace(",", ""))
            context = normalized[max(0, match.start() - 80):match.end() + 20].lower()
            multiplier = 1e9 if "billion" in context else 1e6
            result[key] = value * multiplier
    if result.get("revenue_usd") is not None:
        result["revenue_usd_b"] = result["revenue_usd"] / 1e9
    if result.get("net_income_usd") is not None and "loss" in normalized.lower() and result["net_income_usd"] > 0:
        result["net_income_usd"] *= -1
    result["company_stage"] = "recent_ipo"
    result["source"] = "official_s1_text"
    return result


def structured_period(data):
    text = json.dumps(data, ensure_ascii=False)
    matches = re.findall(r"20\d{2}[.-](?:Q[1-4]|H[12]|FY)", text, re.I)
    return matches[0].replace("-", ".").upper() if matches else None


def structured_financials(data):
    """Normalize Longbridge's latest report without pretending it is SEC data."""
    data = data if isinstance(data, dict) else {}
    indicators = {
        item.get("field_name"): item
        for item in data.get("indicators", [])
        if isinstance(item, dict) and item.get("field_name")
    }
    field_map = {
        "revenue_usd": "operating_revenue",
        "net_income_usd": "net_profit",
        "diluted_eps_usd": "eps",
        "total_assets_usd": "total_assets",
        "total_liabilities_usd": "total_debts",
        "book_value_per_share_usd": "bps",
    }
    result = {"period": structured_period(data), "evidence": {}}
    for output_name, field_name in field_map.items():
        item = indicators.get(field_name)
        if not item:
            continue
        try:
            result[output_name] = float(str(item.get("indicator_value", "")).rstrip("%"))
        except (TypeError, ValueError):
            continue
        result["evidence"][output_name] = {
            "source": "longbridge_financial_report",
            "field_name": field_name,
            "report": data.get("report"),
        }
    for output_name, field_name in (("roe_pct", "roe"), ("net_margin_pct", "net_profit_margin")):
        item = indicators.get(field_name)
        if not item:
            continue
        try:
            result[output_name] = float(str(item.get("indicator_value", "")).rstrip("%"))
        except (TypeError, ValueError):
            pass
    if result.get("revenue_usd") is not None:
        result["revenue_usd_b"] = round(result["revenue_usd"] / 1e9, 6)
    return result


def is_financial_institution(company, structured):
    profile = " ".join(
        str(company.get(key) or "") for key in ("company_name", "name", "profile")
    ).lower()
    return any(term in profile for term in (" bank", "banking", "financial holding", "银行", "金融控股"))


def structured_period_matches_filing(period, filing, segments=None):
    if not period or not filing:
        return False
    report_date = filing.get("report_date") or ""
    try:
        year, month, _day = map(int, report_date.split("-"))
    except (TypeError, ValueError):
        return False
    if filing.get("form_type") in {"10-Q", "10-K"}:
        segments = segments or {}
        segment_period = str(segments.get("report_txt") or "").replace("-", ".").upper()
        segment_end = str(segments.get("fp_end") or "").replace(".", "-")
        return period == segment_period and segment_end == report_date
    if filing.get("form_type") != "8-K" or "2.02" not in str(filing.get("items", "")):
        return False
    expected = {
        1: {f"{year - 1}.FY", f"{year - 1}.Q4"},
        4: {f"{year}.Q1"},
        7: {f"{year}.Q2", f"{year}.H1"},
        10: {f"{year}.Q3"},
    }
    return period in expected.get(month, set())


def extract_eps(corp_actions, official_eps):
    values = []
    for item in corp_actions.get("items", []):
        if item.get("action") != "FinancialReport":
            continue
        match = re.search(r"每股收益\s*([\d.]+)", item.get("act_desc", ""))
        if match:
            values.append(float(match.group(1)))
    if official_eps is not None:
        values = [official_eps] + [value for value in values if abs(value - official_eps) > 0.02]
    return values[:4]


def quarterly_eps_for_ttm(period, eps):
    """Only inject an official EPS value when it represents one quarter."""
    if eps is None or not re.fullmatch(r"20\d{2}\.Q[1-4]", str(period or ""), re.I):
        return None
    return eps


def has_core_financials(data):
    usd_core = all(data.get(field) is not None for field in ("revenue_usd", "net_income_usd", "diluted_eps_usd"))
    native_core = all(data.get(field) is not None for field in ("revenue_native", "net_income_native", "diluted_eps_native", "native_currency"))
    return data.get("period") is not None and (usd_core or native_core)


def vendor_metrics(valuation):
    pe = None
    price = None
    overview = valuation.get("overview") if isinstance(valuation, dict) else {}
    overview = overview if isinstance(overview, dict) else {}
    metrics = overview.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    metric = metrics.get("pe")
    metric = metric if isinstance(metric, dict) else {}
    try:
        pe = float(str(metric.get("metric", "")).rstrip("x"))
    except ValueError:
        pass
    try:
        price = float(metric.get("circle"))
    except (TypeError, ValueError):
        pass
    return pe, price


def fetch_company_facts(cik):
    url = COMPANY_FACTS_URL.format(cik=str(cik).zfill(10))
    # Company Facts values for a filed accession are immutable. A one-day cache
    # avoids repeating the largest SEC request while accession matching still
    # blocks any newly indexed filing absent from the cached payload.
    return get_json(url, cache_ttl=86400, stale_if_error=True), url


def _duration_days(row):
    if not row.get("start") or not row.get("end"):
        return None
    try:
        return (datetime.strptime(row["end"], "%Y-%m-%d") - datetime.strptime(row["start"], "%Y-%m-%d")).days
    except ValueError:
        return None


def _select_duration(candidates, mode):
    if not candidates:
        return None
    with_duration = [(row, _duration_days(row)) for row in candidates]
    with_duration = [(row, days) for row, days in with_duration if days is not None]
    if not with_duration:
        candidates.sort(key=lambda row: row.get("filed", ""), reverse=True)
        return candidates[0]
    if mode == "annual":
        valid = [(row, days) for row, days in with_duration if days >= 300]
        return max(valid or with_duration, key=lambda item: item[1])[0]
    if mode == "ytd":
        return max(with_duration, key=lambda item: item[1])[0]
    valid = [(row, days) for row, days in with_duration if 60 <= days <= 120]
    return min(valid or with_duration, key=lambda item: abs(item[1] - 90))[0]


def fact_entry(payload, tags, filing, units, duration_mode="quarter"):
    facts = payload.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        fact = facts.get(tag)
        if not fact:
            continue
        for unit in units:
            rows = fact.get("units", {}).get(unit, [])
            exact = [row for row in rows if row.get("accn") == filing.get("accession_number")]
            exact_period = [row for row in exact if row.get("end") == filing.get("report_date")]
            dated = [
                row for row in rows
                if row.get("end") == filing.get("report_date") and row.get("form") == filing.get("form_type")
            ]
            candidates = exact_period or exact or dated
            if candidates:
                return _select_duration(candidates, duration_mode), tag, unit
    return None, None, None


def company_facts_financials(payload, filing):
    if not filing or filing.get("form_type") not in {"10-Q", "10-K", "8-K"}:
        return {}
    result = {}
    fields = {
        "revenue_usd": (["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"], ["USD"], "quarter"),
        "operating_income_usd": (["OperatingIncomeLoss"], ["USD"], "quarter"),
        "net_income_usd": (["NetIncomeLoss"], ["USD"], "quarter"),
        "diluted_eps_usd": (["EarningsPerShareDiluted"], ["USD/shares"], "quarter"),
        "operating_cash_flow_usd": (["NetCashProvidedByUsedInOperatingActivities"], ["USD"], "ytd"),
        "capex_usd": (["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], ["USD"], "ytd"),
        "nonoperating_income_usd": (["NonoperatingIncomeExpense"], ["USD"], "quarter"),
        "equity_securities_gain_usd": (["EquitySecuritiesFvNiGainLoss"], ["USD"], "quarter"),
    }
    evidence = {}
    period_row = None
    for name, (tags, units, default_mode) in fields.items():
        mode = "annual" if filing.get("form_type") == "10-K" else default_mode
        row, tag, unit = fact_entry(payload, tags, filing, units, mode)
        if row:
            result[name] = float(row["val"])
            evidence[name] = {
                "tag": tag,
                "unit": unit,
                "accession": row.get("accn"),
                "filed": row.get("filed"),
                "start": row.get("start"),
                "end": row.get("end"),
                "duration_days": _duration_days(row),
                "fy": row.get("fy"),
                "fp": row.get("fp"),
            }
            if name == "revenue_usd":
                period_row = row
    if not evidence:
        return {}
    report_date = filing.get("report_date")
    if period_row and period_row.get("fy") and period_row.get("fp"):
        result["period"] = f"{period_row['fy']}.{period_row['fp']}"
    elif report_date:
        year, month, _day = map(int, report_date.split("-"))
        quarter = {3: "Q1", 6: "Q2", 9: "Q3", 12: "FY"}.get(month, f"M{month}")
        result["period"] = f"{year}.{quarter}"
    if result.get("revenue_usd"):
        result["revenue_usd_b"] = round(result["revenue_usd"] / 1e9, 6)
    if result.get("operating_income_usd") and result.get("revenue_usd"):
        result["operating_margin_pct"] = round(result["operating_income_usd"] / result["revenue_usd"] * 100, 4)
    if result.get("operating_cash_flow_usd") is not None and result.get("capex_usd") is not None:
        result["free_cash_flow_usd"] = result["operating_cash_flow_usd"] - result["capex_usd"]
    result["evidence"] = evidence
    return result


def industry_median_pe(valuation):
    overview = valuation.get("overview") if isinstance(valuation, dict) else {}
    metrics = overview.get("metrics") if isinstance(overview, dict) else {}
    pe = metrics.get("pe") if isinstance(metrics, dict) else {}
    raw = pe.get("industry_median") if isinstance(pe, dict) else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def validate_segments(segments):
    report_text = str(segments.get("report_txt") or segments.get("report") or "")
    fp_end = str(segments.get("fp_end") or "").replace(".", "-")
    data_date = str(segments.get("date") or "")
    if re.fullmatch(r"\d{8}", data_date):
        data_date = f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:]}"
    consistent = not report_text and not fp_end and not data_date
    if report_text or fp_end or data_date:
        consistent = bool(report_text) and (not fp_end or not data_date or fp_end == data_date)
    rows = list(segments.get("business") or []) + list(segments.get("regionals") or [])
    return {
        "report_period": report_text or None,
        "reported_date": fp_end or data_date or None,
        "metadata_consistent": consistent,
        "growth_fields_verified": consistent and any(row.get("yoy") not in (None, "") for row in rows),
    }


def reconcile(symbol, filing_path):
    warnings = []
    filings = json.loads(Path(filing_path).read_text(encoding="utf-8"))
    text = "\n".join(item.get("content", "") for item in filings.get("selected_detail", []))
    selected_filing = filings.get("selected_filing") or {}
    official = extract_registration_financials(text) if selected_filing.get("form_type") in {"S-1", "S-1/A"} else extract_official(text)
    company_facts_url = None
    if not filings.get("foreign_private_issuer") and filings.get("cik"):
        try:
            payload, company_facts_url = fetch_company_facts(filings["cik"])
            xbrl = company_facts_financials(payload, filings.get("selected_filing"))
            if xbrl:
                official = xbrl
                official["source"] = "sec_company_facts"
        except Exception as exc:
            warnings.append(f"SEC Company Facts fallback failed: {exc}")
    # These Longbridge endpoints are independent. Running them concurrently
    # removes additive network latency while preserving the same reconciliation
    # and freshness gates below.
    with ThreadPoolExecutor(max_workers=6) as pool:
        requests = {
            "structured": pool.submit(safe_run_json, "financial-report", symbol, "--latest"),
            "valuation": pool.submit(safe_run_json, "valuation", symbol),
            "corp_actions": pool.submit(safe_run_json, "corp-action", symbol),
            "segments": pool.submit(safe_run_json, "business-segments", symbol),
            "company": pool.submit(safe_run_json, "company", symbol),
            "quote": pool.submit(safe_run_json, "quote", symbol),
        }
        resolved = {name: future.result() for name, future in requests.items()}
    structured = resolved["structured"]
    for name, value in resolved.items():
        if isinstance(value, dict) and value.get("_error"):
            warnings.append(f"Longbridge {name} unavailable; public/SEC fallback used: {value['_error']}")
    structured_latest = structured_financials(structured if isinstance(structured, dict) else {})
    valuation = resolved["valuation"] or {}
    corp_actions = resolved["corp_actions"] or {}
    segments = resolved["segments"] or {}
    company = resolved["company"] if isinstance(resolved["company"], dict) else {}
    if company.get("_error") or not any(company.get(field) for field in ("company_name", "name")):
        title = str(selected_filing.get("title") or selected_filing.get("file_name") or symbol)
        company = {"company_name": title.split("|")[0].strip(), "name": title.split("|")[0].strip(), "data_provider": "SEC"}
    financial_institution = is_financial_institution(company, structured)
    segment_validation = validate_segments(segments)
    if not segment_validation["metadata_consistent"]:
        warnings.append(
            f"Segment metadata conflict: period {segment_validation['report_period']} vs date {segment_validation['reported_date']}"
        )
    old_period = structured_latest.get("period")
    if official.get("period") and old_period and official["period"] != old_period:
        warnings.append(f"财务口径不同：官方文件为 {official['period']}，结构化数据为 {old_period}；本次优先采用官方文件")
    period_aligned = structured_period_matches_filing(old_period, selected_filing, segments)
    if selected_filing.get("form_type") in {"S-1", "S-1/A"}:
        period_aligned = bool(official.get("period"))
        warnings.append("新上市公司尚无 IPO 后 10-Q/10-K；财务证据来自最新 S-1/A，历史数据不可与成熟上市公司直接类比")
    elif (
        selected_filing.get("form_type") == "6-K"
        and selected_filing.get("financial_report")
        and official.get("period") == old_period
    ):
        period_aligned = True
    elif (
        selected_filing.get("form_type") == "8-K"
        and "2.02" in str(selected_filing.get("items", ""))
        and official.get("period") == old_period
    ):
        # An earnings 8-K report_date may be the event/shareholder-meeting date,
        # not the fiscal period end. A period explicitly extracted from the
        # exhibit is stronger evidence when it matches structured data.
        period_aligned = True
    official_core_complete = has_core_financials(official)
    if official_core_complete:
        selected = {"source": official.get("source", "official_filing"), **official}
    elif period_aligned:
        selected = {"source": "longbridge_structured_crosschecked_sec_index", **structured_latest}
        warnings.append("SEC financial filing confirms the period; standardized financial values use period-aligned Longbridge data")
    else:
        selected = {"source": "longbridge_structured_unverified", **structured_latest}
    latest_eps = quarterly_eps_for_ttm(
        selected.get("period"), selected.get("adr_eps_usd") or selected.get("diluted_eps_usd")
    )
    eps = extract_eps(corp_actions, latest_eps)
    vendor_pe, price = vendor_metrics(valuation)
    quote = resolved["quote"]
    public_quote = None
    if isinstance(quote, dict) and quote.get("_error"):
        try:
            public_quote = yahoo_quote(symbol)
            quote = [public_quote]
            warnings.append("Longbridge quote unavailable; current price uses Yahoo public chart")
        except Exception as exc:
            warnings.append(f"Public quote fallback failed: {exc}")
            quote = []
    quote_price = None
    if isinstance(quote, list) and quote:
        try:
            quote_price = float(quote[0].get("last"))
        except (TypeError, ValueError):
            pass
    if quote_price is not None:
        price = quote_price
    ttm_eps = round(sum(eps), 6) if len(eps) == 4 else None
    recomputed_pe = round(price / ttm_eps, 4) if price and ttm_eps else None
    if vendor_pe and recomputed_pe and abs(vendor_pe / recomputed_pe - 1) > 0.05:
        warnings.append(f"Vendor PE {vendor_pe} uses a stale denominator; recomputed TTM PE is {recomputed_pe}")
    median_pe = industry_median_pe(valuation)
    overview = valuation.get("overview") if isinstance(valuation, dict) else {}
    metrics = overview.get("metrics") if isinstance(overview, dict) else {}
    pe = metrics.get("pe") if isinstance(metrics, dict) else {}
    raw_median = pe.get("industry_median") if isinstance(pe, dict) else None
    if raw_median not in (None, "") and median_pe is None:
        warnings.append(f"Invalid non-positive industry median PE rejected: {raw_median}")
    net_income = abs(float(selected.get("net_income_usd") or 0.0))
    nonoperating = abs(float(selected.get("nonoperating_income_usd") or 0.0))
    equity_gain = abs(float(selected.get("equity_securities_gain_usd") or 0.0))
    currency_mismatch = bool(selected.get("native_currency") and selected.get("diluted_eps_usd") is None)
    normalized_warning = bool(
        net_income and (nonoperating / net_income >= 0.10 or equity_gain / net_income >= 0.05)
    ) or currency_mismatch
    if currency_mismatch:
        warnings.append("官方利润以原币披露，缺少同口径美元/ADR EPS，因此禁用美元 TTM PE 重算")
    if normalized_warning:
        warnings.append("GAAP EPS 含较重大非经营性项目，估值结论需要标准化盈利口径")
    if has_core_financials(selected):
        missing_core = []
    else:
        missing_core = ["period", "revenue_usd_or_native", "net_income_usd_or_native", "diluted_eps_usd_or_native"]
    if missing_core:
        warnings.append(f"Missing core official financial fields: {', '.join(missing_core)}")
    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_latest": selected,
        "structured_period": old_period,
        "company_profile": "financial_institution" if financial_institution else "operating_company",
        "official_latest": official,
        "valuation": {
            "vendor_pe_ttm": vendor_pe,
            "price": price,
            "price_source": "longbridge_quote" if quote_price is not None and public_quote is None else ("yahoo_chart_public" if public_quote else "longbridge_valuation"),
            "latest_four_eps_usd": eps,
            "recomputed_ttm_eps_usd": ttm_eps,
            "recomputed_pe_ttm": recomputed_pe,
            "industry_median_pe": median_pe,
            "normalization_required": normalized_warning,
        },
        "segments": {
            "validation": segment_validation,
            "data": segments,
        },
        "warnings": warnings,
        "freshness": {
            "official_period": selected.get("period"),
            "official_filing_date": selected_filing.get("publish_at", "")[:10],
            "structured_period": old_period,
            "latest_quote_checked": quote_price is not None,
            "missing_core_fields": missing_core,
            "period_aligned_with_filing": official_core_complete or period_aligned,
            "status": "CURRENT" if not missing_core and (official_core_complete or period_aligned) and quote_price is not None else "UNVERIFIED",
        },
        "company_facts_url": company_facts_url,
        "source_url": (
            filings.get("selected_detail", [{}])[0].get("source_url")
            if filings.get("selected_detail")
            else ((filings.get("selected_filing") or {}).get("file_urls") or [None])[0]
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--filings", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        data = reconcile(args.symbol.upper(), args.filings)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Success! Reconciled financials written to: {target.resolve()}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
