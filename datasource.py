from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
import requests


def search_symbols(q: str, limit: int = 12) -> List[Dict[str, Any]]:
    q = (q or "").strip()
    if not q:
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://finance.yahoo.com/",
    }
    params = {
        "q": q,
        "quotesCount": limit,
        "newsCount": 0,
        "listsCount": 0,
        "lang": "en-US",
        "region": "US",
    }
    hosts = [
        "https://query2.finance.yahoo.com/v1/finance/search",
        "https://query1.finance.yahoo.com/v1/finance/search",
    ]

    try:
        for url in hosts:
            resp = requests.get(url, params=params, headers=headers, timeout=8)
            ctype = (resp.headers.get("content-type") or "").lower()
            if resp.status_code != 200 or "application/json" not in ctype:
                continue

            j = resp.json() or {}
            quotes = j.get("quotes") or []
            out: List[Dict[str, Any]] = []
            for it in quotes:
                sym = (it.get("symbol") or "").strip()
                name = (it.get("shortname") or it.get("longname") or it.get("name") or sym or "").strip()
                region = (it.get("region") or "").strip()
                currency = (it.get("currency") or "").strip()
                if sym:
                    out.append({"symbol": sym, "name": name, "region": region, "currency": currency})
                if len(out) >= limit:
                    break

            if out:
                return out

        import re as _re
        if _re.fullmatch(r"[A-Za-z\.\-\^]{1,10}", q):
            return [{"symbol": q.upper(), "name": q.upper(), "region": "", "currency": ""}]

        return [{"error": "no_matches", "message": f"No results for '{q}' from Yahoo search."}]

    except Exception as e:
        return [{"error": "search_failed", "message": str(e)}]


def latest_quote(symbol: str) -> Dict[str, Any]:
    symbol = (symbol or "").strip()
    if not symbol:
        return {"_error": True, "_message": "symbol required"}

    try:
        t = yf.Ticker(symbol)
        price: Optional[float] = None
        prev_close: Optional[float] = None
        volume: Optional[float] = None

        try:
            fi = getattr(t, "fast_info", None)
            if fi:
                price = float(fi.get("last_price")) if fi.get("last_price") is not None else None
                prev_close = float(fi.get("previous_close")) if fi.get("previous_close") is not None else None
                volume = float(fi.get("last_volume")) if fi.get("last_volume") is not None else None
        except Exception:
            pass

        if price is None or prev_close is None:
            hist = t.history(period="5d", interval="1d", auto_adjust=False)
            if isinstance(hist, pd.DataFrame) and not hist.empty:
                last_row = hist.dropna(subset=["Close"]).iloc[-1]
                price = float(last_row["Close"])
                if len(hist.dropna(subset=["Close"])) >= 2:
                    prev_close = float(hist.dropna(subset=["Close"]).iloc[-2]["Close"])

                if "Volume" in hist.columns and not pd.isna(last_row.get("Volume")):
                    volume = float(last_row.get("Volume"))

        change = None
        change_percent = None
        if price is not None and prev_close is not None and prev_close != 0:
            change = price - prev_close
            change_percent = (change / prev_close) * 100.0

        return {
            "symbol": symbol,
            "price": price,
            "change": change,
            "change_percent": change_percent,
            "volume": volume,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }
    except Exception as e:
        return {"_error": True, "_message": f"quote_failed: {e}"}


def price_series(symbol: str, interval: str = "daily", lookback: int = 180) -> pd.DataFrame:
    symbol = (symbol or "").strip()
    if not symbol:
        return pd.DataFrame(columns=["date","open","high","low","close","volume"])

    if interval not in (None, "", "daily", "1d"):
        interval = "1d"

    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=max(int(lookback or 0), 1) + 5)

    try:
        hist = yf.download(
            symbol,
            start=start.date(),
            end=(end + timedelta(days=1)).date(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="column",   
        )
        print("yfinance columns:", list(hist.columns))
        print("sample rows:\n", hist.head(3))
    except Exception:
        hist = None

    if hist is None or not isinstance(hist, pd.DataFrame) or hist.empty:
        return pd.DataFrame(columns=["date","open","high","low","close","volume"])

    df = hist.copy()
    if isinstance(df.columns, pd.MultiIndex):
        try:
            lvl0 = [str(c).lower() for c in df.columns.get_level_values(0)]
            lvl1 = [str(c).lower() for c in df.columns.get_level_values(-1)]
            wants = {"open","high","low","close","adj close","volume"}

            if any(x in wants for x in lvl0) and not any(x in wants for x in lvl1):
                df.columns = df.columns.get_level_values(0)
            else:
                df.columns = df.columns.get_level_values(-1)
        except Exception:
            df.columns = ["_".join([str(c) for c in tup if c]) for tup in df.columns]
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    def _pick(col_base: str) -> pd.Series:
        if col_base in df.columns:
            return df[col_base]
        candidates = [c for c in df.columns if c.endswith(f"_{col_base}")]
        if len(candidates) == 1:
            return df[candidates[0]]
        return pd.Series([float('nan')] * len(df), index=df.index)

    index_dates = pd.to_datetime(df.index, errors="coerce")
    try:
        index_dates = index_dates.tz_localize(None)
    except Exception:
        pass

    close_col = "close" if "close" in df.columns else ("adj_close" if "adj_close" in df.columns else None)

    out = pd.DataFrame({
        "date": index_dates,
        "open":   pd.to_numeric(_pick("open"),   errors="coerce"),
        "high":   pd.to_numeric(_pick("high"),   errors="coerce"),
        "low":    pd.to_numeric(_pick("low"),    errors="coerce"),
        "close":  pd.to_numeric(df.get(close_col, pd.Series([float('nan')]*len(df), index=df.index)), errors="coerce"),
        "volume": pd.to_numeric(_pick("volume"), errors="coerce"),
    })
    if lookback and lookback > 0 and len(out) > lookback:
        out = out.tail(lookback).reset_index(drop=True)
    return out
