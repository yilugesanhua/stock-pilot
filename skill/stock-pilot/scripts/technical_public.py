#!/usr/bin/env python3
"""Deterministic technical indicators from the public Yahoo Chart endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import pandas as pd

from public_data import yahoo_chart


PERIODS = {"1mo": "1mo", "3mo": "3mo", "6mo": "6mo", "1y": "1y"}


def frame_for(symbol: str, period: str) -> pd.DataFrame:
    chart = yahoo_chart(symbol, range_name=period, interval="1d")
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(chart.get("timestamp") or [], unit="s", utc=True),
            "open": quote.get("open") or [],
            "high": quote.get("high") or [],
            "low": quote.get("low") or [],
            "close": quote.get("close") or [],
            "volume": quote.get("volume") or [],
        }
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def indicators(frame: pd.DataFrame) -> dict:
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gains / losses.where(losses != 0)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(losses != 0, 100.0)
    rsi = rsi.where(gains != 0, 0.0)
    rsi = rsi.where((gains != 0) | (losses != 0), 50.0)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = true_range.rolling(14).mean()
    current = float(close.iloc[-1])
    output = {
        "rsi": {"value": round(float(rsi.dropna().iloc[-1]), 4), "period": 14} if not rsi.dropna().empty else {},
        "macd": {
            "macd": round(float(macd.iloc[-1]), 4),
            "signal": round(float(signal.iloc[-1]), 4),
            "histogram": round(float((macd - signal).iloc[-1]), 4),
        },
        "atr": {"value": round(float(atr.dropna().iloc[-1]), 4), "percent": round(float(atr.dropna().iloc[-1]) / current * 100, 4)} if not atr.dropna().empty and current else {},
    }
    return output


def result(symbol: str, frame: pd.DataFrame, expected_date: str, period: str) -> dict:
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    current = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) > 1 else current
    returns = {str(days): round(float((current / close.iloc[-1 - days] - 1) * 100), 4) for days in (5, 20, 40) if len(close) > days}
    data_date = str(frame["date"].iloc[-1].date())
    return {
        "symbol": symbol,
        "period": period,
        "price": {"current": round(current, 4), "change": round(current - previous, 4), "change_pct": round((current / previous - 1) * 100, 4) if previous else None},
        "indicators": indicators(frame),
        "price_structure": {
            "sma20": round(float(close.tail(20).mean()), 4) if len(close) >= 20 else None,
            "sma50": round(float(close.tail(50).mean()), 4) if len(close) >= 50 else None,
            "sma200": round(float(close.tail(200).mean()), 4) if len(close) >= 200 else None,
            "returns_pct": returns,
            "support": {str(window): round(float(low.tail(window).min()), 4) for window in (10, 20, 60) if len(low) >= window},
            "resistance": {str(window): round(float(high.tail(window).max()), 4) for window in (10, 20, 60) if len(high) >= window},
            "data_date": data_date,
        },
        "relative_strength_pct_points": {},
        "freshness": {"data_date": data_date, "latest_completed_us_session": expected_date, "is_latest_available": data_date == expected_date, "source": "yahoo_chart_public"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", help="Ticker or comma-separated tickers")
    parser.add_argument("--period", default="1y", choices=tuple(PERIODS))
    parser.add_argument("--indicators", default=None)
    parser.add_argument("--earnings", action="store_true")
    parser.add_argument("--source", choices=("auto", "yahoo", "longbridge"), default="auto")
    parser.add_argument("--output")
    args = parser.parse_args()
    symbols = [item.strip().upper().removesuffix(".US") for item in args.symbol.split(",") if item.strip()]
    try:
        frames = {symbol: frame_for(symbol, args.period) for symbol in symbols}
        if any(frame.empty for frame in frames.values()):
            raise RuntimeError("Yahoo Chart returned no candles")
        expected = max(str(frame["date"].iloc[-1].date()) for frame in frames.values())
        results = [result(symbol, frame, expected, args.period) for symbol, frame in frames.items()]
        by_symbol = {item["symbol"]: item for item in results}
        for item in results:
            own = item["price_structure"]["returns_pct"]
            item["relative_strength_pct_points"] = {
                other: {period: round(own.get(period, 0) - by_symbol[other]["price_structure"]["returns_pct"].get(period, 0), 4) for period in ("5", "20", "40") if period in own and period in by_symbol[other]["price_structure"]["returns_pct"]}
                for other in by_symbol if other != item["symbol"]
            }
        output = {"results": results, "data_source": "yahoo_chart_public", "source_fallback_reason": "Public repository technical adapter", "generated_at": datetime.now(timezone.utc).isoformat(), "data_delay": "Yahoo delay varies"}
        rendered = json.dumps(output, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(rendered)
            print(f"Success! Technical data written to: {args.output}")
        else:
            print(rendered)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
