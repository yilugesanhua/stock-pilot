#!/usr/bin/env python3
"""Collect issuer-appropriate SEC evidence without Form 4 crowd-out."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "tools"
if not (TOOLS / "sec_client.py").exists():
    TOOLS = Path.home() / ".codex" / "skills" / "stock-pilot" / "scripts"
sys.path.insert(0, str(TOOLS))
from sec_client import get_json, user_agent  # noqa: E402
PUBLIC = ROOT / "skill" / "stock-pilot" / "scripts"
if not (PUBLIC / "public_data.py").exists():
    PUBLIC = Path.home() / ".codex" / "skills" / "stock-pilot" / "scripts"
sys.path.insert(0, str(PUBLIC))
from public_data import filing_text, sec_archive_files, sec_company, sec_submissions as public_sec_submissions  # noqa: E402


CORE_FORMS = {"10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "S-1/A"}
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"


def run(args, timeout=90):
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


def form_type(item):
    text = f"{item.get('file_name', '')} {item.get('title', '')}"
    match = re.search(r"(?:^|\s)(10-K|10-Q|8-K|20-F|6-K|S-1/A|S-1|4)(?:\s|-|$)", text, re.I)
    return match.group(1).upper() if match else "OTHER"


def extract_cik(items):
    for item in items:
        text = f"{item.get('file_name', '')} {item.get('title', '')}"
        match = re.search(r"\((\d{10})\)", text)
        if match:
            return match.group(1)
    return None


def sec_submissions(cik):
    url = SEC_SUBMISSIONS.format(cik=cik.zfill(10))
    payload = get_json(url)
    recent = payload["filings"]["recent"]
    items = []
    for index, form in enumerate(recent["form"]):
        if form not in CORE_FORMS:
            continue
        accession = recent["accessionNumber"][index]
        document = recent["primaryDocument"][index]
        accession_path = accession.replace("-", "")
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{document}"
        items.append(
            {
                "form_type": form,
                "file_name": f"{form} - {payload.get('name', '')}",
                "title": f"{payload.get('name', '')} | {form}",
                "publish_at": recent["filingDate"][index] + "T00:00:00Z",
                "report_date": recent["reportDate"][index],
                "accession_number": accession,
                "items": recent["items"][index],
                "file_count": 1,
                "file_urls": [filing_url],
                "source": "sec_submissions",
            }
        )
    return items, url


def is_earnings_8k(item):
    return item.get("form_type") == "8-K" and "2.02" in str(item.get("items", ""))


def looks_like_financial_report(text):
    normalized = re.sub(r"\s+", " ", str(text or "")).lower()
    period = re.search(
        r"(?:first|second|third|fourth) quarter|(?:three|six|nine) months|"
        r"interim (?:financial )?report|quarterly (?:financial )?report|full[- ]year",
        normalized,
    )
    revenue = re.search(r"\brevenue\b|\bnet sales\b|\bsales (?:were|was|increased|decreased)\b", normalized)
    profit = re.search(r"\bnet income\b|\boperating profit\b|\bearnings per share\b|\bdiluted eps\b", normalized)
    return bool(period and revenue and profit)


def select_financial_filing(items, is_fpi):
    if is_fpi:
        return next(
            (item for item in items if item["form_type"] == "6-K" and item.get("financial_report")),
            next((item for item in items if item["form_type"] == "20-F"), None),
        )
    periodic_items = [item for item in items if item["form_type"] in {"10-Q", "10-K"}]
    earnings_items = [item for item in items if is_earnings_8k(item)]
    ranking = lambda item: (
        str(item.get("publish_at", ""))[:10],
        item.get("source") == "sec_submissions",
        str(item.get("publish_at", "")),
    )
    periodic = max(periodic_items, key=ranking) if periodic_items else None
    earnings = max(earnings_items, key=ranking) if earnings_items else None
    if earnings and (not periodic or earnings.get("publish_at", "") > periodic.get("publish_at", "")):
        return earnings
    if periodic:
        return periodic
    # Newly listed issuers may have no post-IPO periodic report yet.
    registration = [item for item in items if item["form_type"] in {"S-1", "S-1/A"}]
    return max(registration, key=ranking) if registration else None


def merge_filings(items, limit):
    items.sort(key=lambda item: (
        str(item.get("publish_at", ""))[:10],
        item.get("source") == "sec_submissions",
        str(item.get("publish_at", "")),
    ), reverse=True)
    merged = []
    indexes = {}
    for item in items:
        urls = item.get("file_urls") or []
        key = urls[0].lower() if urls else item.get("accession_number") or (
            item.get("form_type"), item.get("publish_at"), item.get("title")
        )
        if key in indexes:
            existing = merged[indexes[key]]
            if item.get("id"):
                existing["id"] = item["id"]
                existing["detail_source"] = "longbridge"
            if len(urls) > len(existing.get("file_urls") or []):
                existing["file_urls"] = urls
                existing["file_count"] = item.get("file_count") or len(urls)
            continue
        indexes[key] = len(merged)
        merged.append(dict(item))
    return merged[:limit]


def collect_public(symbol, post_filter_limit, fallback_reason):
    company = sec_company(symbol)
    if not company:
        raise RuntimeError(f"SEC does not recognize {symbol}; Longbridge fallback failed: {fallback_reason}")
    submissions, submissions_url = public_sec_submissions(company["cik"])
    recent = submissions.get("filings", {}).get("recent", {})
    items = []
    for index, form in enumerate(recent.get("form", [])):
        if form not in CORE_FORMS:
            continue
        accession = recent["accessionNumber"][index]
        document = recent["primaryDocument"][index]
        accession_path = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{accession_path}/{document}"
        items.append({
            "form_type": form,
            "file_name": f"{form} - {company['company_name']}",
            "title": f"{company['company_name']} | {form}",
            "publish_at": recent["filingDate"][index] + "T00:00:00Z",
            "report_date": recent["reportDate"][index],
            "items": recent.get("items", [""] * len(recent["form"]))[index],
            "accession_number": accession,
            "file_count": 1,
            "file_urls": [url],
            "source": "sec_submissions_public_fallback",
        })
    filtered = merge_filings(items, post_filter_limit)
    is_fpi = any(item["form_type"] in {"20-F", "6-K"} for item in filtered)
    # FPI quarterly statements often live in Exhibit 99.1 while the 6-K
    # primary document is only a cover page. Bound the scan to recent filings
    # so this remains fast for issuers with hundreds of historical 6-Ks.
    if is_fpi:
        checked = 0
        for item in filtered:
            if item.get("form_type") != "6-K" or checked >= 8:
                continue
            checked += 1
            try:
                names = sec_archive_files(company["cik"], item["accession_number"])
                primary_name = item["file_urls"][0].rsplit("/", 1)[-1]
                base = item["file_urls"][0].rsplit("/", 1)[0]
                candidates = [
                    name for name in names
                    if str(name).lower().endswith((".htm", ".html")) and name != primary_name
                ]
                preferred = [
                    name for name in candidates
                    if re.search(r"earn|quarter|financial|result|guidance|interim|income|report", str(name), re.I)
                ]
                for name in (preferred or candidates)[:4]:
                    url = f"{base}/{name}"
                    text = filing_text(url)[:150000]
                    if looks_like_financial_report(text):
                        item["financial_report"] = True
                        item["financial_file_urls"] = [url]
                        item["financial_report_source"] = "sec_archive_exhibit"
                        break
                if item.get("financial_report"):
                    break
            except Exception:
                continue
    candidate = select_financial_filing(filtered, is_fpi)
    detail = []
    warnings = [f"Longbridge unavailable; SEC public fallback used: {fallback_reason}"]
    if candidate and candidate.get("file_urls"):
        try:
            urls = candidate.get("financial_file_urls") or candidate.get("file_urls")
            detail = [{"file_index": index, "source_url": url, "content": filing_text(url)[:500000]} for index, url in enumerate(urls[:2])]
        except Exception as exc:
            warnings.append(f"SEC filing detail unavailable: {exc}")
    gate = bool(candidate and candidate.get("form_type") in CORE_FORMS)
    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cik": company["cik"],
        "foreign_private_issuer": is_fpi,
        "applicable_forms": {"annual": "20-F", "interim": ["6-K"]} if is_fpi else {"annual": "10-K", "interim": ["10-Q", "8-K"], "new_ipo": ["S-1", "S-1/A"]},
        "quality_gate_passed": gate,
        "queried_count": len(items),
        "filtered_before_limit": len(items),
        "filings": filtered,
        "selected_filing": candidate,
        "selected_detail": detail,
        "warnings": warnings,
        "freshness": {"sec_submissions_checked": True, "latest_filing_date": filtered[0].get("publish_at", "")[:10] if filtered else None, "status": "CURRENT_INDEX"},
        "submissions_url": submissions_url,
        "sec_user_agent": user_agent(),
        "source": "SEC submissions and public filing HTML fallback",
    }


def collect(symbol, post_filter_limit):
    executable = shutil.which("longbridge")
    if not executable:
        return collect_public(symbol, post_filter_limit, "Longbridge CLI not found")
    try:
        # 200 recent filings covers the latest periodic report and registration
        # statement for ordinary US issuers while avoiding a slow full-history scan.
        # SEC submissions remains the authoritative index fallback.
        raw = json.loads(run([executable, "filing", symbol, "--count", "200", "--format", "json"]))
    except Exception as exc:
        return collect_public(symbol, post_filter_limit, str(exc))
    longbridge_filtered = []
    for item in raw:
        current = dict(item)
        current["form_type"] = form_type(current)
        current["source"] = "longbridge"
        urls = current.get("file_urls") or []
        if urls:
            accession_match = re.search(r"/Archives/edgar/data/\d+/(\d{18})/", urls[0], re.I)
            if accession_match:
                digits = accession_match.group(1)
                current["accession_number"] = f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"
        if current["form_type"] in CORE_FORMS:
            longbridge_filtered.append(current)
    warnings = []
    if not os.environ.get("SEC_USER_AGENT"):
        warnings.append("SEC_USER_AGENT is not configured with operator contact information")
    cik = extract_cik(raw)
    official = []
    submissions_url = None
    if cik:
        try:
            official, submissions_url = sec_submissions(cik)
        except Exception as exc:
            warnings.append(f"SEC submissions fallback failed: {exc}")
    merged = official + longbridge_filtered
    filtered = merge_filings(merged, post_filter_limit)
    is_fpi = any(item["form_type"] in {"20-F", "6-K"} for item in filtered)
    applicable = {"annual": "20-F", "interim": ["6-K"]} if is_fpi else {"annual": "10-K", "interim": ["10-Q", "8-K"], "new_ipo": ["S-1", "S-1/A"]}
    detail = []
    if is_fpi:
        for item in filtered:
            if item.get("form_type") != "6-K" or not item.get("id"):
                continue
            texts = []
            count = min(max(int(item.get("file_count") or 1), 1), 3)
            for index in range(count):
                try:
                    texts.append(run([executable, "filing", "detail", symbol, str(item["id"]), "--file-index", str(index)]))
                except Exception as exc:
                    warnings.append(f"FPI filing detail {item.get('id')} index {index} failed: {exc}")
            if looks_like_financial_report("\n".join(texts)):
                item["financial_report"] = True
                item["inspected_content"] = texts
                break
    candidate = select_financial_filing(filtered, is_fpi)
    if candidate and candidate.get("inspected_content"):
        urls = candidate.get("file_urls") or []
        detail = [
            {
                "file_index": index,
                "source_url": urls[index] if index < len(urls) else (urls[0] if urls else None),
                "content": text[:500000],
            }
            for index, text in enumerate(candidate.pop("inspected_content"))
        ]
    elif candidate and candidate.get("id"):
        count = min(max(int(candidate.get("file_count") or 1), 1), 3)
        indexes = range(count) if candidate.get("form_type") in {"S-1", "S-1/A"} else (range(1, count) if count > 1 else range(1))
        for index in indexes:
            try:
                text = run([executable, "filing", "detail", symbol, str(candidate["id"]), "--file-index", str(index)])
                urls = candidate.get("file_urls") or []
                detail.append({
                    "file_index": index,
                    "source_url": urls[index] if index < len(urls) else None,
                    "content": text[:500000],
                })
            except Exception as exc:
                warnings.append(f"Filing detail index {index} failed: {exc}")
    gate = (
        bool(candidate and candidate.get("form_type") in {"20-F", "6-K"})
        if is_fpi
        else any(item["form_type"] in {"10-Q", "10-K"} for item in filtered) or any(is_earnings_8k(item) for item in filtered) or any(item["form_type"] in {"S-1", "S-1/A"} for item in filtered)
    )
    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cik": cik,
        "foreign_private_issuer": is_fpi,
        "applicable_forms": applicable,
        "quality_gate_passed": gate,
        "queried_count": len(raw),
        "filtered_before_limit": len(merged),
        "filings": filtered,
        "selected_filing": candidate,
        "selected_detail": detail,
        "warnings": warnings,
        "freshness": {
            "sec_submissions_checked": bool(official),
            "latest_filing_date": filtered[0].get("publish_at", "")[:10] if filtered else None,
            "status": "CURRENT_INDEX" if official else "DEGRADED",
        },
        "submissions_url": submissions_url,
        "sec_user_agent": user_agent(),
        "source": "SEC submissions index with Longbridge document content fallback",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--limit", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        data = collect(args.symbol.upper(), args.limit)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Success! Filing evidence written to: {target.resolve()}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
