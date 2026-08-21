"""Options Lab underlying universes — indices + equity F&O from instruments."""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

# Index roots shown in the indices universe (not equity F&O).
INDEX_ROOTS = frozenset(
    {
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "NIFTYNXT50",
        "SENSEX",
    }
)

MONTH_CODES = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)

# Fallback when broker instruments are unavailable (liquid NSE F&O names).
EQUITY_FNO_SEED: list[dict[str, Any]] = [
    {"label": "RELIANCE", "symbol": "NSE:RELIANCE", "strike_step": 20, "root": "RELIANCE"},
    {"label": "INFY", "symbol": "NSE:INFY", "strike_step": 25, "root": "INFY"},
    {"label": "TCS", "symbol": "NSE:TCS", "strike_step": 50, "root": "TCS"},
    {"label": "HDFCBANK", "symbol": "NSE:HDFCBANK", "strike_step": 20, "root": "HDFCBANK"},
    {"label": "ICICIBANK", "symbol": "NSE:ICICIBANK", "strike_step": 20, "root": "ICICIBANK"},
    {"label": "SBIN", "symbol": "NSE:SBIN", "strike_step": 10, "root": "SBIN"},
    {"label": "BHARTIARTL", "symbol": "NSE:BHARTIARTL", "strike_step": 20, "root": "BHARTIARTL"},
    {"label": "ITC", "symbol": "NSE:ITC", "strike_step": 5, "root": "ITC"},
    {"label": "LT", "symbol": "NSE:LT", "strike_step": 50, "root": "LT"},
    {"label": "AXISBANK", "symbol": "NSE:AXISBANK", "strike_step": 20, "root": "AXISBANK"},
    {"label": "KOTAKBANK", "symbol": "NSE:KOTAKBANK", "strike_step": 20, "root": "KOTAKBANK"},
    {"label": "MARUTI", "symbol": "NSE:MARUTI", "strike_step": 100, "root": "MARUTI"},
    {"label": "BAJFINANCE", "symbol": "NSE:BAJFINANCE", "strike_step": 100, "root": "BAJFINANCE"},
    {"label": "SUNPHARMA", "symbol": "NSE:SUNPHARMA", "strike_step": 20, "root": "SUNPHARMA"},
    {"label": "TITAN", "symbol": "NSE:TITAN", "strike_step": 50, "root": "TITAN"},
    {"label": "ASIANPAINT", "symbol": "NSE:ASIANPAINT", "strike_step": 50, "root": "ASIANPAINT"},
    {"label": "WIPRO", "symbol": "NSE:WIPRO", "strike_step": 5, "root": "WIPRO"},
    {"label": "HCLTECH", "symbol": "NSE:HCLTECH", "strike_step": 20, "root": "HCLTECH"},
    {"label": "ONGC", "symbol": "NSE:ONGC", "strike_step": 5, "root": "ONGC"},
    {"label": "NTPC", "symbol": "NSE:NTPC", "strike_step": 5, "root": "NTPC"},
]

_FUT_RE = re.compile(
    r"^(?P<root>[A-Z0-9]+)(?P<yy>\d{2})(?P<mon>[A-Z]{3})FUT$",
    re.IGNORECASE,
)


def equity_root_from_underlying(underlying_symbol: str) -> str | None:
    raw = (underlying_symbol or "").strip().upper()
    if not raw:
        return None
    if ":" in raw:
        _, raw = raw.split(":", 1)
    raw = raw.strip()
    # Spot indices / labels
    aliases = {
        "NIFTY 50": "NIFTY",
        "NIFTY50": "NIFTY",
        "BANK NIFTY": "BANKNIFTY",
        "FIN NIFTY": "FINNIFTY",
        "MIDCP NIFTY": "MIDCPNIFTY",
    }
    return aliases.get(raw, raw.replace(" ", ""))


def infer_strike_step(strikes: list[float | int]) -> int:
    vals = sorted({float(s) for s in strikes if s is not None and float(s) > 0})
    if len(vals) < 2:
        return 5
    diffs = [round(vals[i + 1] - vals[i], 6) for i in range(len(vals) - 1)]
    diffs = [d for d in diffs if d > 0]
    if not diffs:
        return 5
    # Prefer the most common positive gap; fall back to gcd of integer gaps.
    counts: dict[float, int] = {}
    for d in diffs:
        counts[d] = counts.get(d, 0) + 1
    mode = max(counts.items(), key=lambda item: (item[1], -item[0]))[0]
    if mode >= 1:
        return max(1, int(round(mode)))
    scaled = [int(round(d * 100)) for d in diffs]
    g = scaled[0]
    for n in scaled[1:]:
        g = math.gcd(g, n)
    return max(1, int(round(g / 100))) if g > 0 else 5


def _last_thursday(year: int, month: int):
    from calendar import monthcalendar

    weeks = monthcalendar(year, month)
    thursdays = [week[3] for week in weeks if week[3] != 0]
    return thursdays[-1]


def _active_fut_month(when: datetime | None = None) -> tuple[int, int]:
    now = when or datetime.now(ZoneInfo("Asia/Kolkata"))
    ist = now.astimezone(ZoneInfo("Asia/Kolkata")) if now.tzinfo else now.replace(
        tzinfo=ZoneInfo("Asia/Kolkata")
    )
    ref = ist.date()
    year, month = ref.year, ref.month
    if ref.day > _last_thursday(year, month):
        month += 1
        if month > 12:
            month = 1
            year += 1
    return year, month


def suggest_equity_fut_symbol(
    underlying_symbol: str,
    *,
    when: datetime | None = None,
    exchange: str = "NFO",
) -> str:
    root = equity_root_from_underlying(underlying_symbol)
    if not root or root in INDEX_ROOTS:
        return ""
    year, month = _active_fut_month(when)
    yy = str(year)[-2:]
    mon = MONTH_CODES[month - 1]
    return f"{exchange}:{root}{yy}{mon}FUT"


def parse_nfo_instrument_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build equity F&O presets from Kite instrument dict rows."""
    by_root: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip().upper()
        itype = str(row.get("instrument_type") or "").strip().upper()
        segment = str(row.get("segment") or "").strip().upper()
        tradingsymbol = str(row.get("tradingsymbol") or "").strip().upper()
        exchange = str(row.get("exchange") or "NFO").strip().upper() or "NFO"
        if not name or name in INDEX_ROOTS:
            continue
        if itype not in {"CE", "PE", "FUT"}:
            continue
        # Options should be on an OPT segment when segment is present.
        if itype in {"CE", "PE"} and segment and "OPT" not in segment:
            continue
        bucket = by_root.setdefault(
            name,
            {
                "root": name,
                "strikes": set(),
                "futs": [],
                "lot_size": None,
                "exchange": exchange,
            },
        )
        if itype in {"CE", "PE"}:
            try:
                strike = float(row.get("strike") or 0)
            except (TypeError, ValueError):
                strike = 0.0
            if strike > 0:
                bucket["strikes"].add(strike)
        if itype == "FUT" and tradingsymbol.endswith("FUT"):
            expiry = str(row.get("expiry") or "")
            bucket["futs"].append((expiry, f"{exchange}:{tradingsymbol}"))
        try:
            lot = int(float(row.get("lot_size") or 0))
        except (TypeError, ValueError):
            lot = 0
        if lot > 0 and not bucket["lot_size"]:
            bucket["lot_size"] = lot

    out: list[dict[str, Any]] = []
    for root, bucket in sorted(by_root.items()):
        strikes = sorted(bucket["strikes"])
        if not strikes:
            continue
        futs = sorted(bucket["futs"], key=lambda item: item[0] or "9999")
        fut_symbol = futs[0][1] if futs else suggest_equity_fut_symbol(f"NSE:{root}")
        out.append(
            {
                "label": root,
                "symbol": f"NSE:{root}",
                "strike_step": infer_strike_step(strikes),
                "root": root,
                "fut_symbol": fut_symbol,
                "lot_size": bucket["lot_size"],
                "universe": "equities",
                "source": "instruments",
            }
        )
    return out


def parse_nfo_instruments_csv(csv_text: str) -> list[dict[str, Any]]:
    """Build equity F&O presets from a Kite `/instruments/NFO` CSV dump."""
    if not csv_text or not str(csv_text).strip():
        return []
    text = str(csv_text)
    # Tool wrappers may nest CSV under JSON-ish payloads.
    if text.lstrip().startswith("{"):
        return []
    reader = csv.DictReader(io.StringIO(text))
    return parse_nfo_instrument_rows(list(reader))


def extract_instruments_rows(payload: Any) -> list[dict[str, Any]] | None:
    """Normalize toolkit payloads to instrument dict rows."""
    if payload is None:
        return None
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return rows or None
    if isinstance(payload, dict):
        for key in ("data", "instruments", "result", "items", "records"):
            nested = payload.get(key)
            found = extract_instruments_rows(nested)
            if found:
                return found
        # Single-instrument dict
        if payload.get("tradingsymbol") or payload.get("instrument_token"):
            return [payload]
    return None


def extract_instruments_csv(payload: Any) -> str | None:
    """Pull CSV text out of toolkit / HTTP wrapper shapes."""
    if payload is None:
        return None
    if isinstance(payload, str) and ("instrument_token" in payload or "tradingsymbol" in payload):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        try:
            text = payload.decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
        return text if "tradingsymbol" in text else None
    if isinstance(payload, list):
        rows = extract_instruments_rows(payload)
        if not rows:
            return None
        # Round-trip via CSV so callers that only know CSV keep working.
        fieldnames = [
            "instrument_token",
            "exchange_token",
            "tradingsymbol",
            "name",
            "last_price",
            "expiry",
            "strike",
            "tick_size",
            "lot_size",
            "instrument_type",
            "segment",
            "exchange",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
        return buf.getvalue()
    if isinstance(payload, dict):
        for key in ("csv", "data", "text", "body", "content"):
            nested = payload.get(key)
            found = extract_instruments_csv(nested)
            if found:
                return found
        rows = extract_instruments_rows(payload)
        if rows:
            return extract_instruments_csv(rows)
    return None


def merge_presets(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        for preset in group:
            symbol = str(preset.get("symbol") or "").strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            out.append(dict(preset))
    return out
