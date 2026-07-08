"""Stdlib-only HTTP fetch + on-disk cache for external macro series (PLANv2 Section 1b).

No third-party deps: urllib, ssl, gzip, hashlib, csv only. Portable as-is to the
locked-down work PC. Every fetched series is written as a tidy CSV with an explicit
`available_date` column so the PIT as-of join (Section 4b) stays honest.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import os
import ssl
import time
import urllib.error
import urllib.request

DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")
USER_AGENT = "runoff-model/1.0 (stdlib urllib)"


def _cache_path(url: str, cache_dir: str) -> str:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, key + ".gz")


def http_get(url, *, ssl_relaxed=False, accept=None, retries=3, backoff=2.0,
             cache_dir=DEFAULT_CACHE, use_cache=True, timeout=30):
    """GET `url`, return raw bytes. Caches the payload (gzip) on disk so a flaky
    endpoint never blocks a re-run.

    `ssl_relaxed=True` is required for bank-of-algeria.dz, whose TLS chain is
    missing an intermediate cert (verified 2026-06). Use it ONLY for that host.
    `accept` sets the Accept header (e.g. SDMX-JSON for the IMF API).
    """
    if use_cache:
        cp = _cache_path(url, cache_dir)
        if os.path.exists(cp):
            with gzip.open(cp, "rb") as fh:
                return fh.read()

    ctx = None
    if ssl_relaxed:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                data = resp.read()
            if use_cache:
                os.makedirs(cache_dir, exist_ok=True)
                with gzip.open(_cache_path(url, cache_dir), "wb") as fh:
                    fh.write(data)
            return data
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url}\n  last error: {last}")


def write_series_csv(rows, path, header=("ref_period", "value", "available_date")):
    """Write tidy series rows to CSV. `rows` = iterable of (ref_period, value, available_date)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path
