"""Sumber data makro & news real-time (tanpa API key tambahan).

- News: Google News RSS (keyless) — di-filter 24 jam terakhir.
- Quote: TwelveData /quote (free tier) — change %, 52-week, volume sebagai konteks makro.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

from . import config

TIMEOUT = 20
CRYPTO = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "DOT", "AVAX", "LINK",
          "MATIC", "LTC", "TRX", "TON", "SHIB"}


def _build_query(symbol: str) -> str:
    s = symbol.upper().strip()
    if "/" in s:
        base, quote = s.split("/", 1)
        if base in CRYPTO or quote in CRYPTO:
            return f"{base} {quote} cryptocurrency price"
        return f"{base} {quote} forex currency market"
    if s in CRYPTO:
        return f"{s} cryptocurrency price"
    return f"{s} stock market"


def fetch_news(symbol: str, hours: int = 24, limit: int = 15) -> tuple[list[dict], str]:
    """Return (news_items, note). news_items = [{title, published_at, url}]."""
    query = _build_query(symbol)
    url = ("https://news.google.com/rss/search?"
           f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en")
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        return [], f"Gagal mengambil news: {e}"

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return [], "Respons news tidak dapat di-parse."

    now = datetime.now(timezone.utc)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if now - dt > timedelta(hours=hours):
            continue
        items.append({"title": title, "published_at": dt.isoformat(timespec="minutes"),
                      "url": link, "age_hours": round((now - dt).total_seconds() / 3600, 1)})
    items.sort(key=lambda x: x["published_at"], reverse=True)
    items = items[:limit]
    if not items:
        return [], f"Tidak ada news terdeteksi dalam {hours} jam terakhir."
    return items, f"{len(items)} berita terdeteksi dalam {hours} jam terakhir."


def fetch_quote(symbol: str) -> tuple[dict | None, str]:
    """Konteks makro cepat dari TwelveData /quote (change %, 52w, volume)."""
    api_key = config.env("TWELVEDATA_API_KEY").strip()
    if not api_key:
        return None, "TWELVEDATA_API_KEY belum diisi — konteks quote dilewati."
    base = config.env("TWELVEDATA_BASE_URL", "https://api.twelvedata.com").rstrip("/")
    try:
        r = requests.get(f"{base}/quote", params={"symbol": symbol, "apikey": api_key},
                         timeout=TIMEOUT)
        data = r.json()
    except requests.RequestException as e:
        return None, f"Gagal ambil quote: {e}"
    except ValueError:
        return None, "Quote bukan JSON."

    if isinstance(data, dict) and data.get("code") and data.get("code") != 200:
        return None, f"Quote: {data.get('message', 'error')}"
    if not isinstance(data, dict) or not data.get("close"):
        return None, "Data quote tidak tersedia."

    out = {
        "close": data.get("close"),
        "change": data.get("change"),
        "percent_change": data.get("percent_change"),
        "volume": data.get("volume"),
    }
    fw = data.get("fifty_two_week") or {}
    if isinstance(fw, dict):
        out["fifty_two_week_low"] = fw.get("low")
        out["fifty_two_week_high"] = fw.get("high")
    return out, "OK"
