#!/usr/bin/env python3
"""Collect recent news and catalysts with non-blocking detail fallbacks."""

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
PUBLIC = Path(__file__).resolve().parents[4] / "skill" / "stock-pilot" / "scripts"
if not (PUBLIC / "public_data.py").exists():
    PUBLIC = Path.home() / ".codex" / "skills" / "stock-pilot" / "scripts"
sys.path.insert(0, str(PUBLIC))
from public_data import rss_news, sec_company  # noqa: E402

SOCIAL_HEALTH_TTL_SECONDS = 600


def recent_social_health(path):
    if not path.exists() or datetime.now().timestamp() - path.stat().st_mtime >= SOCIAL_HEALTH_TTL_SECONDS:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def run(args, timeout=60):
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


def lb_json(executable, *args):
    return json.loads(run([executable, *args, "--format", "json"]))


def relevance_keywords(symbol, company):
    keywords = {symbol.split(".")[0].lower()}
    stopwords = {"inc", "corp", "corporation", "company", "limited", "holdings", "group", "class"}
    for field in ("company_name", "name"):
        value = str(company.get(field, "")).lower()
        if value:
            keywords.add(re.sub(r"[^a-z0-9]+", " ", value).strip())
            keywords.update(
                token for token in re.findall(r"[a-z0-9]+", value)
                if len(token) >= 4 and token not in stopwords
            )
    return {keyword for keyword in keywords if keyword}


def estimated_earnings_catalysts(relevant_news, confirmed):
    """Return a clearly unconfirmed earnings clue from recent headlines."""
    earnings_clues = [
        item for item in relevant_news
        if re.search(r"\b(?:ahead of|before|upcoming|next)\b.{0,24}\bearnings\b|\bearnings\b.{0,24}\b(?:ahead|upcoming|next)\b", str(item.get("title", "")), re.I)
    ]
    if not earnings_clues or confirmed:
        return []
    clue = max(earnings_clues, key=lambda item: str(item.get("published_at", "")))
    return [{
        "date": None,
        "event": "新闻标题提示可能临近财报（日期未确认）",
        "status": "estimated",
        "source": clue.get("url"),
        "title": clue.get("title"),
    }]


def collect(symbol, filing_path, detail_count):
    executable = shutil.which("longbridge")
    warnings = []
    company = {}
    news = []
    public_news_url = None
    try:
        if not executable:
            raise RuntimeError("Longbridge CLI not found")
        news = lb_json(executable, "news", symbol, "--count", "20")
        company = lb_json(executable, "company", symbol) or {}
    except Exception as exc:
        company = sec_company(symbol) or {}
        news, public_news_url = rss_news(symbol, company.get("company_name") or company.get("name"), 20)
        warnings.append(f"Longbridge news unavailable; Google News RSS metadata used: {exc}")
    if not any(company.get(field) for field in ("company_name", "name")):
        static = lb_json(executable, "static", symbol) or []
        if isinstance(static, list) and static:
            company = {**company, **static[0]}
    keywords = relevance_keywords(symbol, company)
    for item in news:
        title = str(item.get("title", "")).lower()
        item["relevant"] = any(keyword in title for keyword in keywords)
    # Details are supplementary. Fetch only relevant headlines and do so in parallel;
    # freshness and catalyst gates rely on the complete metadata list above.
    detail_items = [item for item in news if item.get("relevant")][:detail_count]
    def fetch_detail(item):
        try:
            return item, {"content": run([executable, "news", "detail", str(item["id"])], 15)[:30000]}, "available", None
        except Exception as exc:
            return item, {}, "unavailable", str(exc)

    detail_pool = ThreadPoolExecutor(max_workers=min(4, max(1, len(detail_items))))
    detail_futures = [detail_pool.submit(fetch_detail, item) for item in detail_items]
    try:
        actions = lb_json(executable, "corp-action", symbol) if executable else {}
    except Exception as exc:
        actions = {}
        warnings.append(f"Longbridge corporate actions unavailable; SEC filing catalysts retained: {exc}")
    today = dt.date.today()
    end = today + dt.timedelta(weeks=8)
    confirmed = []
    for item in actions.get("items", []):
        try:
            event_date = dt.datetime.strptime(str(item.get("date")), "%Y%m%d").date()
        except ValueError:
            continue
        if today <= event_date <= end:
            confirmed.append({"date": event_date.isoformat(), "event": item.get("act_desc") or item.get("act_type"), "status": "confirmed", "source": "Longbridge corporate action"})
    filings = json.loads(Path(filing_path).read_text(encoding="utf-8"))
    estimated = []
    backend = {"available": bool(shutil.which("agent-reach")), "routing_verified": False, "verified": False, "searches": {}}
    if backend["available"] and not public_news_url:
        try:
            cache = Path(tempfile.gettempdir()) / "stock-pilot-agent-reach-doctor.json"
            opencli = shutil.which("opencli.cmd") or shutil.which("opencli")
            if cache.exists():
                output = cache.read_text(encoding="utf-8")
                if datetime.now().timestamp() - cache.stat().st_mtime >= 21600:
                    warnings.append("Agent Reach 路由缓存超过 6 小时；常规分析不阻塞刷新，显式 doctor 时再更新")
            else:
                output = json.dumps({
                    "twitter": {"status": "ok" if opencli else "off", "active_backend": "OpenCLI" if opencli else None},
                    "reddit": {"status": "ok" if opencli else "off", "active_backend": "OpenCLI" if opencli else None},
                })
            if output:
                backend["routing_verified"] = True
                backend["doctor"] = json.loads(output)
                query = f"{symbol.split('.')[0]} {company.get('company_name') or company.get('name') or ''} stock earnings"
                search_jobs = {}
                social_cache = Path(tempfile.gettempdir()) / f"stock-pilot-social-{symbol.split('.')[0].lower()}.json"
                health_cache = Path(tempfile.gettempdir()) / "stock-pilot-social-health.json"
                social_health = recent_social_health(health_cache)
                if social_cache.exists() and datetime.now().timestamp() - social_cache.stat().st_mtime < 3600:
                    backend["searches"] = json.loads(social_cache.read_text(encoding="utf-8"))
                with ThreadPoolExecutor(max_workers=2) as pool:
                    for platform in ("twitter", "reddit"):
                        channel = backend["doctor"].get(platform, {})
                        cached = backend["searches"].get(platform, {})
                        if cached.get("status") == "available":
                            continue
                        if social_health.get(platform, {}).get("status") == "temporarily_unavailable":
                            backend["searches"][platform] = {
                                "status": "temporarily_unavailable_cached",
                                "query": query,
                                "error": f"{platform} search recently timed out; retry deferred for up to 10 minutes",
                            }
                            continue
                        if opencli and channel.get("status") == "ok" and channel.get("active_backend") == "OpenCLI":
                            search_jobs[pool.submit(run, [opencli, platform, "search", query, "-f", "yaml"], 6)] = platform
                    for future in as_completed(search_jobs):
                        platform = search_jobs[future]
                        try:
                            raw = future.result()
                            backend["searches"][platform] = {"status": "available", "query": query, "raw": raw[:60000]}
                            social_health.pop(platform, None)
                        except Exception as exc:
                            # Social context is supplementary; one bounded attempt avoids
                            # delaying a report when a platform is unavailable.
                            backend["searches"][platform] = {"status": "unavailable", "query": query, "error": str(exc)}
                            # Health state is platform-wide. Never persist a ticker-specific
                            # query/error here because the cache is reused by other symbols.
                            social_health[platform] = {"status": "temporarily_unavailable"}
                health_cache.write_text(json.dumps(social_health, ensure_ascii=False), encoding="utf-8")
                if any(item.get("status") == "available" for item in backend["searches"].values()):
                    backend["verified"] = True
                    social_cache.write_text(json.dumps(backend["searches"], ensure_ascii=False), encoding="utf-8")
            else:
                warnings.append("Agent Reach returned no active backend; Longbridge/SEC fallback used")
        except Exception as exc:
            warnings.append(f"Agent Reach unavailable; Longbridge/SEC fallback used: {exc}")
    elif public_news_url:
        warnings.append("公开新闻回退模式跳过实时 X/Reddit 搜索；社交内容仅作补充线索")
    else:
        warnings.append("Agent Reach CLI unavailable; Longbridge/SEC fallback used")
    for future in as_completed(detail_futures):
        item, fields, status, error = future.result()
        item.update(fields)
        item["detail_status"] = status
        if error:
            item["detail_error"] = error
            warnings.append(f"News detail {item.get('id')} unavailable; list metadata retained")
    detail_pool.shutdown(wait=True)
    relevant_news = [item for item in news if item.get("relevant")]
    # A headline can flag an earnings risk without proving an official date.
    # Keep it explicitly estimated and never promote it to a confirmed catalyst.
    estimated.extend(estimated_earnings_catalysts(relevant_news, confirmed))
    newest_news = max((str(item.get("published_at", "")) for item in relevant_news), default="")
    newest_date = newest_news[:10] if newest_news else None
    try:
        news_age_days = (today - datetime.strptime(newest_date, "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        news_age_days = None
    news_current = news_age_days is not None and news_age_days <= 7
    if not news_current:
        warnings.append("No news item within the required seven-day window")
    return {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": today.isoformat(), "end": end.isoformat()},
        "news": news,
        "catalysts": {"confirmed": confirmed, "estimated": estimated},
        "agent_reach": backend,
        "freshness": {
            "status": "CURRENT" if news_current else "STALE",
            "newest_news_at": newest_news or None,
            "news_age_days": news_age_days,
            "required_window_days": 7,
            "relevant_news_count": len(relevant_news),
            "relevance_keywords": sorted(keywords),
            "catalyst_window_end": end.isoformat(),
        },
        "warnings": warnings,
        "public_news_url": locals().get("public_news_url"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--filings", required=True)
    parser.add_argument("--detail-count", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        data = collect(args.symbol.upper(), args.filings, args.detail_count)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Success! News and catalysts written to: {target.resolve()}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
