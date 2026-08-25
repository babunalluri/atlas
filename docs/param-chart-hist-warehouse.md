# Param Chart hist warehouse (PAUSED)

**Status:** Paused — do **not** implement until product explicitly reopens this.  
**Date:** 2026-08-25  
**Context:** Desk discussion after Param Chart on-demand candle dumps shipped.

## What we have today (shipped)

Param Chart stores Kite hist **only for instruments the desk actually loads**:

| Prefix | Purpose |
| --- | --- |
| `param-chart/candles/<instrument_token>/…` | OHLC dump per token + interval (1m / 1H / 1D / 1W·1M year packs) |
| `param-chart/metrics/<tenant_id>/<YYYY-MM>.json` | Shared-checklist overlay history (tenant) |
| `param-chart/symbol-tokens/<exchange>/<tradingsymbol>.json` | CE/PE token map so premiums work after expiry |

There is **no** pre-seeded archive of Nifty 50 equities or “all indices.” Spot/CE/PE appear for a month only after Soft load / Refresh (and CE/PE need a live or previously saved token).

## Proposed (not built) — “all Nifty 50 / all indices” warehouse

Scheduled backfill of **cash/index underlyings only** (not full F&O chains):

| Universe | Approx count |
| --- | ---: |
| Nifty 50 equities | 50 |
| Desk indices (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX, INDIA VIX, …) | 10–20 |
| **Total** | **~60–70** |

### Phased scope (if resumed)

1. **Phase 1:** ~70 names × **1D × 5 years** → ~10–15 MB cold store; one overnight job.  
2. **Phase 2:** Add **1H for last 1–2 years** (or current year only).  
3. **Out of scope unless requested:** all CE/PE / all strikes — API time and contract churn dominate; use `symbol-tokens` + on-demand dumps instead.

### Cost sketch (why we paused on “cost”, not need)

| Item | Ballpark |
| --- | --- |
| Object Storage (OCI / MinIO) | **≪ $1–2 / mo** at ~100 MB–few GB |
| Kite Connect | Existing plan (hist typically included; no per-bar fee) |
| Backfill wall-clock | **Tens of minutes → hours** (sandbox + rate limits), not cloud $ |
| Daily refresh | Negligible (~70 daily bars) |
| Engineering | ~0.5–2 days (universe list, job, retries, prefixes) |
| Full F&O hist | **Much higher** — do not start here |

Storage is cheap. The real cost is **rate-limited backfill time**, ops, and scope creep into options.

## Resume checklist

When un-pausing:

- [ ] Confirm universe list (Nifty 50 constituents source of truth + index symbols).  
- [ ] Confirm years + intervals (recommend Phase 1 only first).  
- [ ] Job placement: backend management command / worker, not SSE path.  
- [ ] Reuse `param_chart_candle_store` keys; never stampede from Param Chart stream.  
- [ ] Document lifecycle rules for `param-chart/candles/` on OCI.  
- [ ] Explicitly exclude full option-chain warehouse unless separately approved.

## Related code

- `apps/backend/src/app/domains/param_chart_candle_store.py`  
- `apps/backend/src/app/domains/param_chart_token_store.py`  
- `apps/backend/src/app/domains/param_chart_metrics_store.py`  
- `docs/oci-deployment.md` → “Param Chart candle dumps”
