"""Public US-market fallbacks used when Longbridge is unavailable."""

from __future__ import annotations

import html
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _user_agent(url):
    value = os.environ.get("SEC_USER_AGENT", "")
    if "sec.gov" in url.lower() and not ("@" in value or "http" in value):
        raise RuntimeError("SEC_USER_AGENT must contain a real maintainer email or project URL before accessing sec.gov")
    return value or "stock-pilot/1.0"


def _get(url, headers=None, timeout=20):
    merged = {"User-Agent": _user_agent(url), "Accept": "*/*"}
    merged.update(headers or {})
    request = Request(url, headers=merged)
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=max(timeout, 30)) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"Public data request failed after 3 attempts: {last_error}")


def _cache_path(name):
    root = Path(tempfile.gettempdir()) / "stock-pilot-public-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def _cached_json(name, url, ttl=86400):
    path = _cache_path(name)
    if path.exists() and time.time() - path.stat().st_mtime <= ttl:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    payload = json.loads(_get(url).decode("utf-8"))
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def sec_company(symbol):
    """Resolve a US ticker to SEC CIK/name without Longbridge."""
    code = symbol.upper().removesuffix(".US")
    sec_code = sec_lookup_code(code)
    payload = _cached_json("sec-company-tickers.json", SEC_TICKERS_URL)
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    indexes = {name: index for index, name in enumerate(fields)}
    for row in rows:
        if str(row[indexes.get("ticker", 2)]).upper() == sec_code:
            cik = str(row[indexes.get("cik", 0)]).zfill(10)
            name = row[indexes.get("name", 1)]
            return {
                "company_name": name,
                "name": name,
                "cik": cik,
                "exchange": row[indexes.get("exchange", 3)],
                "source": "sec_company_tickers_exchange",
            }
    return None


def sec_submissions(cik):
    url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
    return _cached_json(f"sec-submissions-{str(cik).zfill(10)}.json", url, ttl=300), url


def sec_lookup_code(symbol):
    return str(symbol).upper().removesuffix(".US").replace(".", "-")


def yahoo_code(symbol):
    return str(symbol).upper().removesuffix(".US").replace(".", "-")


def filing_text(url):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = _cache_path(f"filing-{key}.txt")
    if path.exists() and time.time() - path.stat().st_mtime <= 86400:
        return path.read_text(encoding="utf-8")
    raw = _get(url, headers={"Accept": "text/html,application/xhtml+xml"})
    parser = _HTMLText()
    parser.feed(raw.decode("utf-8", errors="replace"))
    text = re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()
    path.write_text(text, encoding="utf-8")
    return text


def sec_archive_files(cik, accession):
    """Return SEC archive filenames for an accession, including exhibits."""
    accession_path = str(accession).replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/index.json"
    payload = _cached_json(f"sec-archive-{int(cik)}-{accession_path}.json", url, ttl=86400)
    return [item.get("name") for item in (payload.get("directory") or {}).get("item", []) if item.get("name")]


def yahoo_chart(symbol, range_name="1y", interval="1d"):
    code = yahoo_code(symbol)
    url = YAHOO_CHART_URL.format(symbol=quote(code, safe=".-")) + "?" + urlencode({
        "range": range_name,
        "interval": interval,
        "events": "div,splits",
        "includePrePost": "false",
    })
    # Short cross-process cache removes duplicate quote/history calls inside a
    # single run while keeping the freshness SLA effectively real-time.
    payload = _cached_json(
        "yahoo-" + hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json",
        url,
        ttl=60,
    )
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo chart returned no data for {code}")
    return result[0]


def yahoo_quote(symbol):
    chart = yahoo_chart(symbol, range_name="5d", interval="1d")
    meta = chart.get("meta") or {}
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    previous = meta.get("previousClose") or meta.get("chartPreviousClose")
    timestamp = meta.get("regularMarketTime")
    data_date = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat() if timestamp else None
    if price is None:
        closes = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        price = next((value for value in reversed(closes) if value is not None), None)
    if price is None:
        raise RuntimeError(f"Yahoo quote returned no price for {symbol}")
    return {
        "symbol": symbol.upper(),
        "last": str(price),
        "prev_close": str(previous) if previous is not None else None,
        "data_date": data_date,
        "source": "yahoo_chart_public",
        "source_url": YAHOO_CHART_URL.format(symbol=quote(symbol.upper().removesuffix(".US"), safe=".-")),
    }


def rss_news(symbol, company_name=None, limit=20):
    query = f"{symbol.upper().removesuffix('.US')} stock"
    if company_name:
        query += f" {company_name}"
    url = GOOGLE_NEWS_RSS_URL + "?" + urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    # Google News rejects the terse bot UA intermittently; use a normal browser
    # UA for this public RSS endpoint. No account or API key is involved.
    root = ET.fromstring(_get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"}).decode("utf-8", errors="replace"))
    items = []
    for item in root.findall("./channel/item")[:limit]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        published = item.findtext("pubDate") or ""
        try:
            published_at = parsedate_to_datetime(published).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            published_at = None
        source = item.find("source")
        items.append({
            "title": title,
            "url": link,
            "published_at": published_at,
            "source": source.text if source is not None else "Google News RSS",
            "relevant": True,
            "detail_status": "metadata_only",
        })
    return items, url
