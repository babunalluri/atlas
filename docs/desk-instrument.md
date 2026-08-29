# Desk instrument board (`desk_instrument`)

**Status:** Shipped (2026-08-28)  
**Scope:** Shared instrument identity across Signal Engine, Options Lab, and Param Chart on the trading desk.

---

## What it is

`desk_instrument` is a **small Postgres blob** inside the Signal-engine tool settings. It answers one question for the whole desk:

> **What are we trading right now?** (underlying, FUT, strike step, ATM CE/PE)

It is **not** the Signal matrix, **not** Redis, and **not** the options chain. Each desk still owns its own data on top of this identity layer.

| Layer | Role | Storage | Holds |
|-------|------|---------|--------|
| **`desk_instrument`** | Shared **identity** | Postgres (`desk_instrument` key in signal tool settings) | `underlying_symbol`, `fut_symbol`, `strike_step`, `ce_symbol`, `pe_symbol`, `atm` |
| **Signal matrix** | Checklist **metrics per instrument** | Postgres config + Redis halves (`globals` + per-row) | PCR, OI, pass/fail, per-instrument rows |
| **Signal Redis snapshot** | **Live tick frame** | Redis (`merged_frame`) | Spot, ATM, CE/PE LTP, live checklist state |

**Analogy:** `desk_instrument` = nameplate on the desk · matrix = checklist grid · Redis snapshot = live TV feed.

**Long-term direction:** [Desk architecture roadmap](desk-architecture-roadmap.md) — instrument-first landing, unified per-instrument matrix, phased backend unification.

---

## Where it lives

All keys sit in the **same** Signal-engine tool settings document (Postgres):

```text
signal tool settings
├── signal_engine config     ← matrix / engine / pinned instruments
├── desk_instrument          ← shared nameplate (this doc)
├── options_lab              ← Lab-specific settings (sandbox, wings, …)
└── param_chart              ← Chart month/strike/OHLC settings
```

Canonical backend module: `apps/backend/src/app/domains/desk_instrument.py`  
Frontend handoff: `apps/web/src/components/domains/desk-instrument.ts`

---

## Writers and readers

### Writers (on identity PATCH)

When any desk PATCH touches identity fields (`underlying_symbol`, `fut_symbol`, `strike_step`, CE/PE, Param Chart `strike`), the backend merges into `desk_instrument`:

| Desk | Module | `source` tag |
|------|--------|----------------|
| Signal Engine | `signal_engine.py` → `update_admin_config` | `"signal"` |
| Options Lab | `options_lab.py` → `update_admin_config` | `"options-lab"` |
| Param Chart | `param_chart.py` → `update_admin_config` | `"param-chart"` |

Empty `underlying_symbol` is **not** written (guard in `desk_instrument_tool_patch`).

### Readers (on GET / internal read)

| Desk | Behavior |
|------|----------|
| **Options Lab** | Board **wins** over nested `options_lab` on read (`fill_only=False` in `options_lab._read_config`). |
| **Param Chart** | Board **wins** over nested `param_chart` instrument fields when board has an underlying. |
| **Signal (admin GET)** | Board merged in `get_admin_config()` via `merge_desk_instrument_into_signal()` — UI config matches board. |
| **Signal (engine / ticker)** | Still reads signal nest via `_load_config()` → `_load_setup()` **without** board merge — **known gap**; worker may run a different underlying than the UI until PATCH saves. See [roadmap Phase 2](desk-architecture-roadmap.md#phase-2--one-identity-m-in-progress). |

### Lab chain seed from Signal (Live only)

When Lab underlying **matches** Signal and Signal has a **fresh** live row in Redis (`snapshot_stale` false, age ≤ `SNAPSHOT_FRESH_MS`), `OptionsLabService._signal_chain_seed()` calls `signal_engine_cache.merged_frame()` and reuses `nifty_ltp` / ATM (+ CE/PE when present). Stale rows after engine stop are ignored — Lab falls back to `get_quote(underlying)`.

---

## Frontend same-tab sync (Step 4)

Within one browser tab/session:

1. **`sessionStorage`** key `atlas-desk-instrument` — last published selection.
2. **`CustomEvent`** `atlas-desk-instrument` — instant handoff between mounted panels.

Each desk **publishes** on instrument picks and **subscribes** to others (skips its own `source` to avoid loops). Apply paths PATCH config only; they do not publish back.

| Panel | Mounted when | Publishes on |
|-------|----------------|--------------|
| Signal | Always (desk tab hidden OK) | Preset/screener, custom underlying, setup bar |
| Param Chart | Always (hidden OK) | Underlying `<select>` |
| Options Lab | **Only when Lab tab open** | Setup bar / screener underlying pick (not on passive tab open) |

**Note:** Lab does **not** publish live chain CE/PE — ATM rolls must not rewrite Param Chart month packs.

After a **full page reload**, `sessionStorage` is gone; Postgres `desk_instrument` + each desk’s nested config apply.

---

## Walkthrough: Signal on NIFTY → open Options Lab (Live)

Assumptions: NIFTY selected in Signal, engine **running**, Lab config has no underlying yet (or matches NIFTY).

### Phase 0 — Signal already running

1. User picks NIFTY in Signal → `PATCH /admin/signal-engine/config`
2. Backend writes Signal config **and** `desk_instrument` (NIFTY, FUT, strike step).
3. Signal ticks write Redis matrix halves, e.g.:
   - `atlas:signals:{tenant}:globals`
   - `atlas:signals:{tenant}:row:{instrument_key}`
   - `atlas:signals:{tenant}:primary_row` (optional)

Signal SSE is independent of Lab (Lab tab not mounted yet).

### Phase 1 — User clicks Options Lab tab

- `OptionsLabPanel` lazy-loads and mounts (`active=true`).
- `useOptionsLabConfigAutosave` enables.

### Phase 2 — Config load (fast path)

**Request:** `GET /admin/options-lab/config`

1. **No** live NFO `get_instruments` dump on config GET (`allow_live_fetch=False` — seed presets only).
2. `_read_config()` loads `options_lab` nest; if underlying empty, merges `desk_instrument`.
3. Frontend sets `configReady` when underlying + FUT exist. Suggested FUT persist runs in **background** (`void persist`) — does not block chain SSE.

### Phase 3 — Same-session handoff (optional)

If Signal published NIFTY earlier in this tab, Lab’s subscribe effect reads `sessionStorage` and `patchConfig` to match.

### Phase 4 — Chain stream starts

**Request:** `GET /admin/options-lab/stream?wings=…`

1. Steady state: Options Lab Redis cache only (no Postgres per tick).
2. Cold path: `chain_state_for_stream`:
   - If Signal watcher alive → cap `wings` to `MIN_WINGS` (pilot load saver).
   - `_read_config()` again (board fill if needed).
   - Try Lab cache key `options_lab:chain:{wings}:{fingerprint}`.
   - On miss → `chain_snapshot()`:
     - **`_signal_chain_seed`**: `merged_frame(tenant, instrument=NIFTY)` → reads row `atm`, `nifty_ltp`, CE/PE from Redis when aligned (not `underlying.ltp`).
     - Else: `get_quote(underlying)` for spot.
     - Build strike grid → one `get_quote` batch for CE/PE chain rows.
     - Cache result → SSE ~8 Hz to UI.

### Phase 5 — Lab → other desks (identity only)

Lab publishes **underlying / FUT / strike step** only — not live chain CE/PE on every ATM roll. Param Chart keeps its fixed-strike-per-month contracts; Signal keeps its own auto-ATM path.

```text
┌─────────────────────────────────────────────────────────────┐
│  POSTGRES (Signal tool settings)                            │
│  ├─ signal config                                           │
│  ├─ desk_instrument    ← shared nameplate                   │
│  ├─ options_lab                                             │
│  └─ param_chart                                             │
└─────────────────────────────────────────────────────────────┘
         │                              │
         │ fill if Lab empty              │ Signal ticks
         ▼                              ▼
┌─────────────────┐           ┌─────────────────────┐
│ Options Lab     │◄─ seed ───│ REDIS Signal        │
│ chain build     │  spot/ATM │ globals + row:NIFTY │
└─────────────────┘           └─────────────────────┘
         │
         ▼
┌─────────────────┐
│ REDIS Lab chain │
│ options_lab:…   │
└─────────────────┘
```

---

## What this fixed vs other changes

| Problem | Fix | `desk_instrument` role |
|---------|-----|-------------------------|
| “Loading Options Lab setup…” for minutes | Skip NFO `get_instruments` on config GET | None (separate commit) |
| Pilot CPU/RAM when Lab unused | Lazy-load Lab tab; unmount when hidden | Reduces duplicate cold-start work when opened |
| Margin errors with full tracebacks | Sanitize margin warnings in UI | None |
| Signal works but Lab feels disconnected | Shared board + chain seed | **Primary** — identity sync + reuse Signal Redis spot/ATM |
| Re-pick instrument in every tab | Frontend publish/subscribe + board persist | **Primary** — same session + Postgres bootstrap |

---

## Board precedence (intentional asymmetry)

| Desk | Backend read rule | Frontend subscribe |
|------|-------------------|-------------------|
| **Param Chart** | Board **wins** over nested `param_chart` (`merge_desk_instrument_into_chart`) | Identity only (underlying, FUT, strike step) — never live chain CE/PE |
| **Options Lab** | Board **wins** over nested `options_lab` (`fill_only=False`) | Applies identity from other desks |
| **Signal (GET)** | Board merged in `get_admin_config()` only | Same-tab handoff; CE/PE when explicitly published |
| **Signal (engine)** | Nest via `_load_config()` — **no board merge yet** | N/A |

`ce_symbol` / `pe_symbol` on the Postgres board reflect the last **manual** identity PATCH. Signal **auto-ATM** updates the signal config directly and does **not** write the board (otherwise ATM rolls would invalidate Param Chart month packs).

---

## Limits (by design)

- **Engine vs UI drift:** Signal admin GET merges the board; `_load_config()` / ticker still use the nest until Phase 2 worker merge (see [roadmap](desk-architecture-roadmap.md)).
- **Multi-window:** Chart overlay and Lab chain caches are tenant-singleton — multi-instrument windows clobber until Phase 0 cache keys ship.
- **Same-tab handoff:** `sessionStorage` + `CustomEvent` do not cross windows; use URL per window for instrument-first routing.
- **Partial sync:** FUT/strike-only edits in one desk may not propagate via frontend bus until `patchIdentity()` centralizes publish.
- **Margin / broker auth:** Not solved by `desk_instrument`; Live margin still depends on Kite toolkit binding.
- **Lab tab open:** Chain + sandbox work still runs when Lab is active; board does not remove that cost.

---

## Related code

| Area | Path |
|------|------|
| Board helpers | `apps/backend/src/app/domains/desk_instrument.py` |
| Lab read/seed | `apps/backend/src/app/domains/options_lab.py` (`_read_config`, `_signal_chain_seed`, `chain_snapshot`) |
| Signal write | `apps/backend/src/app/domains/signal_engine.py` (`update_admin_config`) |
| Param Chart merge | `apps/backend/src/app/domains/param_chart.py` |
| Redis merge | `apps/backend/src/app/domains/signal_engine_cache.py` (`merged_frame`) |
| Frontend handoff | `apps/web/src/components/domains/desk-instrument.ts` |
| Workspace lifecycle | `apps/web/src/components/domains/TraderWorkspace.tsx` (three-tab desk retired) |
| Tests | `apps/backend/tests/test_desk_instrument.py`, `apps/web/src/components/domains/desk-instrument.test.ts` |

---

## See also

- [Options Lab — market profile](options-lab-market-profile.md) — future US/IN abstraction (not shipped).
- [Tech debt](tech-debt.md) — pilot perf, lazy panels, sandbox trim.
