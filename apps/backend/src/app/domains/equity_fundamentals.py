"""Market cap and P/E for desk instruments (NSE public endpoints, slow tier).

No broker toolkit exposes fundamentals — Kite and Groww both serve quotes and
books only — so these come from NSE's public site, under the same rules the
rest of the slow tier follows (see ``signal_engine_nse``):

- Never called from the ~8 Hz stream. The screener's 60 s cache is the fastest
  thing that reads this, and the values themselves move on a daily cadence.
- Market cap for every F&O equity arrives in ONE ``equity-stockIndices`` call,
  and index P/E in one ``allIndices`` call. Only equity P/E is per-symbol.
- Per-symbol P/E is therefore filled a few names per refresh (``PE_FILL_BUDGET``)
  rather than N requests at once, so a 200-name universe cannot stampede NSE.
  Callers render "—" for a name that has not been filled yet.
- Serve stale on failure, and back off after an error rather than retrying hot.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
# Fundamentals move on a daily cadence; anything shorter just burns requests.
FUNDAMENTALS_TTL_SECONDS = 21_600  # 6 h
FUNDAMENTALS_COOLDOWN_SECONDS = 1_800
# Per-symbol P/E fetches allowed per refresh. Keeps a cold 200-name universe to
# a trickle instead of a burst NSE would rate-limit.
PE_FILL_BUDGET = 5

_FNO_INDEX = "SECURITIES IN F&O"

# {symbol_root: {"market_cap": float | None, "pe": float | None, "at": ts}}
_equity: dict[str, dict[str, Any]] = {}
_index_pe: dict[str, float] = {}
_state: dict[str, float] = {
    "market_cap_at": 0.0,
    "index_pe_at": 0.0,
    "cooldown_until": 0.0,
}
_lock = threading.Lock()


def _session() -> Any | None:
    try:
        from curl_cffi import requests as curl_requests

        return curl_requests.Session(impersonate="chrome")
    except ImportError:
        return None


def _nse_get(path: str, session: Any) -> Any | None:
    if session is None:
        return None
    try:
        # NSE only serves the JSON APIs to a session that already holds the
        # homepage cookies.
        session.get(f"{NSE_BASE}/", timeout=15)
        resp = session.get(f"{NSE_BASE}{path}", timeout=20)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — public endpoint, never fatal
        logger.warning("nse_fundamentals_failed path=%s err=%s", path, exc)
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        out = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    # NSE sends 0 for "not applicable" (loss-making names have no P/E).
    return out if out > 0 else None


def symbol_root(symbol: str) -> str:
    """``NSE:RELIANCE`` → ``RELIANCE``; index labels pass through upcased."""
    raw = (symbol or "").strip().upper()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    return raw.strip()


def _parse_market_caps(body: Any) -> dict[str, float]:
    """Free-float market cap (₹ crore) per symbol from ``equity-stockIndices``."""
    out: dict[str, float] = {}
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        cap = _to_float(row.get("ffmc"))
        if cap is not None:
            out[sym] = cap
    return out


def _parse_index_pe(body: Any) -> dict[str, float]:
    """P/E per index from ``allIndices``. Indices have no market cap."""
    out: dict[str, float] = {}
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        pe = _to_float(row.get("pe"))
        if pe is None:
            continue
        for key in ("indexSymbol", "index", "indexName"):
            name = str(row.get(key) or "").strip().upper()
            if name:
                out[name] = pe
    return out


def _parse_symbol_pe(body: Any) -> float | None:
    """Trailing P/E from ``quote-equity`` metadata."""
    if not isinstance(body, dict):
        return None
    meta = body.get("metadata")
    if isinstance(meta, dict):
        pe = _to_float(meta.get("pdSymbolPe"))
        if pe is not None:
            return pe
    info = body.get("info")
    if isinstance(info, dict):
        return _to_float(info.get("pdSymbolPe"))
    return None


def _fetch(roots: list[str], *, force: bool) -> None:
    """Refresh caches in place. Holds ``_lock``; callers must not."""
    ts = time.time()
    if not force and _state["cooldown_until"] > ts:
        return

    session = _session()
    if session is None:
        _state["cooldown_until"] = ts + FUNDAMENTALS_COOLDOWN_SECONDS
        return

    progressed = False
    failed = False

    # One call covers market cap for every F&O equity.
    if force or (ts - _state["market_cap_at"]) >= FUNDAMENTALS_TTL_SECONDS:
        body = _nse_get(
            f"/api/equity-stockIndices?index={quote(_FNO_INDEX, safe='')}",
            session,
        )
        caps = _parse_market_caps(body)
        if caps:
            for sym, cap in caps.items():
                entry = _equity.setdefault(sym, {"market_cap": None, "pe": None})
                entry["market_cap"] = cap
            _state["market_cap_at"] = ts
            progressed = True
        else:
            failed = True

    # One call covers P/E for every index.
    if force or (ts - _state["index_pe_at"]) >= FUNDAMENTALS_TTL_SECONDS:
        body = _nse_get("/api/allIndices", session)
        pes = _parse_index_pe(body)
        if pes:
            _index_pe.update(pes)
            _state["index_pe_at"] = ts
            progressed = True
        else:
            failed = True

    # Equity P/E is the only per-symbol call, so spend a small budget per pass
    # on the names that are still missing one.
    stale = [root for root in roots if _pe_fill_due(_equity.get(root), ts)]
    for root in stale[:PE_FILL_BUDGET]:
        # Roots carry '&' (M&M, M&MFIN); unencoded they truncate the query.
        body = _nse_get(f"/api/quote-equity?symbol={quote(root, safe='')}", session)
        pe = _parse_symbol_pe(body)
        entry = _equity.setdefault(root, {"market_cap": None, "pe": None})
        # Stamp either way: a name NSE has no P/E for (a loss-maker) must not be
        # retried on every single pass.
        entry["at"] = ts
        if pe is not None:
            entry["pe"] = pe
            progressed = True

    # Back off after ANY failed call, not only a fully fruitless pass: a bulk
    # call that fails leaves its timestamp at 0, so without this every later
    # pass would retry it and re-pay the timeout.
    if failed or not progressed:
        _state["cooldown_until"] = ts + FUNDAMENTALS_COOLDOWN_SECONDS


def _pe_fill_due(entry: dict[str, Any] | None, ts: float) -> bool:
    """True when this name's P/E is worth (re)fetching.

    Keyed on the attempt stamp, not on whether a P/E is present: NSE has no P/E
    for a loss-making name, so testing ``pe is None`` first made those names
    permanently stale — re-fetched every pass and keeping ``refresh_due`` True
    on every screener request, which is exactly what the stamp was added to
    stop.
    """
    if entry is None:
        return True
    return (ts - float(entry.get("at") or 0)) >= FUNDAMENTALS_TTL_SECONDS


def _split_roots(
    symbols: list[str],
    index_symbols: tuple[str, ...] | frozenset[str] | set[str],
) -> tuple[set[str], list[str]]:
    index_roots = {symbol_root(s) for s in index_symbols}
    equity_roots = [
        root
        for root in (symbol_root(s) for s in symbols)
        if root and root not in index_roots
    ]
    return index_roots, equity_roots


def read_fundamentals(
    symbols: list[str],
    *,
    index_symbols: tuple[str, ...] | frozenset[str] | set[str] = (),
) -> dict[str, dict[str, float | None]]:
    """``{symbol: {"market_cap": ₹cr | None, "pe_ratio": float | None}}`` from cache.

    Pure cache read — never touches the network, so it is safe on a request
    path. A name with nothing cached yet comes back with both fields None so
    the caller renders "—" rather than hiding the row.

    ``index_symbols`` names the entries that are indices, whose P/E comes from
    ``allIndices`` rather than a per-symbol quote.
    """
    index_roots, _ = _split_roots(symbols, index_symbols)

    out: dict[str, dict[str, float | None]] = {}
    for symbol in symbols:
        root = symbol_root(symbol)
        if not root:
            continue
        # An index never has an equity entry; reading one would let a stray
        # quote-equity response shadow the authoritative index P/E.
        entry = {} if root in index_roots else (_equity.get(root) or {})
        pe = entry.get("pe")
        if pe is None:
            pe = _index_pe.get(root)
        out[symbol] = {
            "market_cap": entry.get("market_cap"),
            "pe_ratio": pe,
        }
    return out


def refresh_fundamentals(
    symbols: list[str],
    *,
    index_symbols: tuple[str, ...] | frozenset[str] | set[str] = (),
    force: bool = False,
) -> None:
    """Refresh the caches from NSE. **Blocking, and slow on a cold cache.**

    ``_nse_get`` warms the NSE homepage before every call, so a cold pass is
    several minutes in the worst case. Never await this from a request: run it
    as a background task (see ``apply_screener_fundamentals``). Concurrent
    callers return immediately rather than queueing.
    """
    _, equity_roots = _split_roots(symbols, index_symbols)

    if not _lock.acquire(blocking=False):
        return
    try:
        _fetch(equity_roots, force=force)
    except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
        logger.warning("nse_fundamentals_refresh_failed err=%s", exc)
    finally:
        _lock.release()


def refresh_due(
    symbols: list[str],
    *,
    index_symbols: tuple[str, ...] | frozenset[str] | set[str] = (),
) -> bool:
    """True when a refresh would actually do work, so callers can skip the task."""
    ts = time.time()
    if _state["cooldown_until"] > ts:
        return False
    if _lock.locked():
        return False
    if (ts - _state["market_cap_at"]) >= FUNDAMENTALS_TTL_SECONDS:
        return True
    if (ts - _state["index_pe_at"]) >= FUNDAMENTALS_TTL_SECONDS:
        return True
    _, equity_roots = _split_roots(symbols, index_symbols)
    return any(_pe_fill_due(_equity.get(root), ts) for root in equity_roots)


def mock_fundamentals(symbols: list[str]) -> dict[str, dict[str, float | None]]:
    """Deterministic stand-ins so mock desks render the columns."""
    out: dict[str, dict[str, float | None]] = {}
    for idx, symbol in enumerate(symbols):
        out[symbol] = {
            "market_cap": round(85_000 + idx * 12_500.0, 2),
            "pe_ratio": round(18.5 + idx * 1.7, 2),
        }
    return out


def fundamentals_status() -> dict[str, Any]:
    return {
        "equities_cached": len(_equity),
        "indices_cached": len(_index_pe),
        "market_cap_age_s": round(time.time() - _state["market_cap_at"], 1)
        if _state["market_cap_at"]
        else None,
        "cooldown_active": _state["cooldown_until"] > time.time(),
    }


def reset_fundamentals_for_tests() -> None:
    _equity.clear()
    _index_pe.clear()
    _state.update({"market_cap_at": 0.0, "index_pe_at": 0.0, "cooldown_until": 0.0})
