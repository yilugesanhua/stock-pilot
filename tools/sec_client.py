#!/usr/bin/env python3
"""Shared SEC JSON client with cross-process throttling, retries, and caching."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


REQUESTS_PER_SECOND = 5.0
CACHE_TTL_SECONDS = 300


class RateLimitError(RuntimeError):
    pass


def user_agent() -> str:
    value = os.environ.get("SEC_USER_AGENT", "")
    if "@" not in value and "http" not in value:
        raise RuntimeError("SEC_USER_AGENT must contain a real maintainer email or project URL")
    return value


def _acquire_lock(name: str, timeout: float = 30.0) -> tuple[int, Path]:
    lock = Path(tempfile.gettempdir()) / f"stock-pilot-{name}.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY), lock
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:
                    if time.time() - lock.stat().st_mtime > 60:
                        lock.unlink()
                        continue
                except FileNotFoundError:
                    continue
                raise RuntimeError(f"Timed out acquiring {name} rate-limit lock")
            time.sleep(0.05)


def _wait_for_rate_limit() -> None:
    fd, lock = _acquire_lock("sec-rate")
    state = lock.with_suffix(".state")
    try:
        last = 0.0
        if state.exists():
            try:
                last = float(state.read_text(encoding="ascii"))
            except ValueError:
                last = 0.0
        delay = max(0.0, 1.0 / REQUESTS_PER_SECOND - (time.monotonic() - last))
        if delay:
            time.sleep(delay)
        state.write_text(str(time.monotonic()), encoding="ascii")
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def _cache_path(url: str) -> Path:
    root = Path(tempfile.gettempdir()) / "stock-pilot-sec-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"


def get_json(url: str, retries: int = 3, cache_ttl: int = CACHE_TTL_SECONDS, stale_if_error: bool = False) -> dict:
    cache = _cache_path(url)
    if cache.exists() and time.time() - cache.stat().st_mtime <= cache_ttl:
        return json.loads(cache.read_text(encoding="utf-8"))
    for attempt in range(retries):
        _wait_for_rate_limit()
        request = urlrequest.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": user_agent()},
        )
        try:
            with urlrequest.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            cache.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
            if exc.code == 429:
                if attempt + 1 == retries:
                    raise RateLimitError(
                        f"SEC HTTP 429 from {url} after {retries} attempts at {REQUESTS_PER_SECOND} req/s: {body}"
                    ) from exc
            elif exc.code < 500:
                raise RuntimeError(f"SEC HTTP {exc.code} from {url}: {body}") from exc
            if attempt + 1 < retries:
                wait = 2**attempt
                print(f"SEC retry {attempt + 1}/{retries} in {wait}s for {url}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"SEC HTTP {exc.code} from {url} after {retries} attempts: {body}") from exc
        except urlerror.URLError as exc:
            if attempt + 1 == retries:
                raise RuntimeError(f"SEC connection failed for {url} after {retries} attempts: {exc}") from exc
            wait = 2**attempt
            print(f"SEC connection retry {attempt + 1}/{retries} in {wait}s for {url}", file=sys.stderr)
            time.sleep(wait)
        except TimeoutError as exc:
            # The submissions index is small and normally responds quickly. A
            # slow read cannot prove freshness, so fail closed without stacking
            # multiple long retries; the caller will preserve Longbridge evidence
            # and block an action recommendation.
            if stale_if_error and cache.exists():
                return json.loads(cache.read_text(encoding="utf-8"))
            raise RuntimeError(f"SEC connection timed out after 12s for {url}") from exc
    raise RuntimeError(f"SEC request failed: {url}")
