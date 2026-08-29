# Trading desk architecture roadmap

**Status:** Proposed · **Phase 2 in progress** · **End-user Lab SKU scoped** (2026-08-28)  
**Owner:** Trading desk (Signal / Lab / Chart)  
**Audience:** Product + engineering

**Review verdict (2026-08-28):** Direction is right; **not safe to execute in the order originally written**. Lab is componentized and REST-backed — the split is RBAC + routing + stream semantics, not a rewrite. Fix sequencing (F1), complete the RBAC table (F2–F3), correct Lab cache spec (F4), and resolve identity authority (F6) before admin P0 engine merge (F5).

---

## How to read this doc

| Section | Use when |
|---------|----------|
| **[Review findings](#review-findings-code-verified)** | What was wrong in v1 of this plan and how to fix it |
| **[Implement now — End-user Lab SKU](#implement-now--end-user-lab-sku)** | Shipping Lab + Automation for **normal users** |
| **[Implement now — Admin desk fixes](#implement-now--admin-desk-fixes)** | Operator desk (instrument sync, Signal engine) |
| **[Current state](#current-state)** | Today’s coupling and gaps |
| **[Long-term phases](#long-term-phases)** | Full instrument-first admin desk |
| **[Reference](#reference)** | Instruments, cache, Kite, data model |
| **[Navigation map](desk-navigation-map.html)** | Visual route tree |

**Sizing:** **S** ≈ days · **M** ≈ 1–2 weeks · **L** ≈ multi-sprint.

---

## Decision log

| Date | Decision |
|------|----------|
| 2026-08-28 | **One instrument per window** — multi-window multi-instrument once stream/watchers are fixed. |
| 2026-08-28 | **End-user Lab SKU** — Lab + Automation only; Signal / Chart admin-only. |
| 2026-08-28 | **Lab sub-tools** — separate windows OK; Ideas→Backtest needs URL or channel. |
| 2026-08-28 | **MVP pilot constraint:** **one instrument per tenant** until stream accepts `?underlying=` (E1). URL instrument in UI is cosmetic until then. |
| 2026-08-28 | **Resolve open decision #6** (`desk_instrument` vs URL) **before** admin P0 engine merge — merge shape depends on authority model (F6). |
| 2026-08-28 | **Lab snapshot cache** is already fingerprinted by underlying — E1 is **stream + watcher**, not cache key (F4). |

---

## Review findings (code-verified)

Spot-checked against `options_lab.py`, `options_lab_cache.py`, `signal_engine.py`, `OptionsLabPanel.tsx`, `test_options_lab_stream.py:568`.

### F1 — Track A4 conflicts with “skip E1” — **breaks URL instrument promise**

`GET /admin/options-lab/stream` accepts only `wings`, not `underlying`:

```python
# apps/backend/src/app/api/options_lab.py
async def stream_options_chain(..., wings: int = Query(...)):
    payload = await chain_frame_from_cache(tenant_key, wings=wings)
```

`chain_frame_from_cache` resolves the instrument via **`get_fingerprint(tenant_id)`** → tenant **`options_lab` config**, not the request URL. End users cannot `PATCH /config` (403). So `/lab/BSE:SENSEX` would still stream whatever the tenant config holds.

**Fix (pick one for MVP):**

| Option | MVP |
|--------|-----|
| **A** — Pilot **one instrument per tenant**; drop URL instrument from A4 exit criteria | ✅ Fastest |
| **B** — Pull **E1** into MVP: `?underlying=` on stream/chain + per-instrument watcher | ~3 weeks |

### F2 — Books vs Track B — **already broken for customers today**

- `/portfolios/*` uses **`AdminContext`** (`options_lab.py:288–347`).
- ~~**Books** button is **outside** `{!readOnly ? …}` — customers can click, get **403**.~~ Fixed: Books and Flows moved inside the admin guard.

**Fix:** Add **`/portfolios/*`** to B1 **or** remove Books from end-user SKU (A5).

### F3 — Track B route table incomplete — **builder/backtest 403**

Missing from original B table; both **`AdminContext`** today:

| Route | Used by |
|-------|---------|
| `POST /margins` (`options_lab.py:358`) | Strategy builder |
| `GET /iv-history` (`options_lab.py:101`) | Backtest IV points |

Without these in B1, “build strategy” and “save backtest” fail for end users even after screener/bots open.

### F4 — Lab chain cache mis-specified in E1

**Snapshot keys are already instrument-scoped** via fingerprint:

```python
# options_lab.py
def cache_fingerprint(self) -> str:
    return f"{self.underlying_symbol}|{self.fut_symbol}|{self.strike_step}|{int(self.mock)}"

# options_lab_cache.py
# atlas:options-lab:{tenant}:snapshot:{wings}:{fingerprint}
```

Different underlyings → **different Redis keys** — they do not clobber each other.

**Real gap:** **`_watchers` is tenant-singleton** (`options_lab_cache.py:215` — keyed by `tenant_id` only). Stream cold path reads **`service._read_config()`** (tenant config), not URL.

**Correct E1:** Add **`?underlying=`** (or instrument) to **stream + chain GET**; make **watcher meta per instrument**; optional read path that builds fingerprint from query param without PATCHing org config.

### F5 — Admin P0 memoization trap (not in original doc)

`_load_config()` → `_load_setup()` memoizes `"setup"` in Redis (`signal_engine.py:2344–2367`). Invalidation (`delete_metric(tenant_id, "setup")`) runs on **Signal** config patches, not on Lab/board writes. `options_lab.update_admin_config` clears **`ol_cache` fingerprints** but **not** `"setup"`.

Merging board into `_load_config()` without an **invalidation contract** leaves the engine stale until setup TTL expires after Lab/board writes.

**P0 must include:** invalidate `"setup"` (and document which writers trigger it) when board or Lab identity changes.

### F6 — P0 scheduled before identity authority decision

Open decision **#6** (`desk_instrument` vs URL) gates Phase 2 shape. P0 engine merge assumes **board is authoritative** for a **tenant-singleton** engine — but `_matrix_extra_configs` already supports **multiple instruments** (pinned + watched). Resolving #6 first is cheap and may simplify or redirect P0.

---

## Implement now — End-user Lab SKU

**Product:** Options Lab (chain + builder) + Automation (Ideas, Backtest, Bot). No Signal / Param Chart.

**Architecture fit:** ✅ Lab componentized, REST-backed — RBAC + routing problem.  
**Works today:** ❌ UI hides automation; most routes admin-only; Books 403 for customers (F2).

### Recommended ship order

```text
1. Resolve MVP mode: one-instrument-per-tenant (Option A) OR E1 in MVP (Option B)
2. B1 — RBAC (screener, backtests, bots, portfolios, margins, iv-history)
3. A1–A3 — TraderWorkspace shell, instrument-first routes, automationEnabled
4. C1–C2 — same-window overlays
5. A4 — URL instrument ONLY if E1 shipped; else tenant config instrument only
6. C3+ — sub-routes / separate windows
```

### Today vs target

| Capability | End user today | Lab SKU (target) |
|------------|----------------|------------------|
| Desk shell | 3 tabs: Signal, Chart, Lab | Lab only |
| Chain stream | ✅ View (tenant config instrument) | Same until E1 |
| URL `/lab/{instrument}` | N/A | **Cosmetic until E1** (F1) |
| Ideas / Backtest / Bot | Hidden + 403 | Enabled after B1 |
| Books | Clickable → **403** (F2) | Works after B1 portfolios |
| Builder margins / Backtest IV | N/A (admin) | B1: margins + iv-history |
| Signal / Chart | View-only tabs | Not in SKU |

### Target routes (end user)

```text
/t/{slug}/lab                         instrument picker (MVP: sets tenant config via admin, or display only)
/t/{slug}/lab/{instrument}            chain + builder + Books
/t/{slug}/lab/{instrument}/ideas      screener ranks
/t/{slug}/lab/{instrument}/backtest     model runs (+ ?template=)
/t/{slug}/lab/{instrument}/bots         paper bots

Admin (unchanged): /t/{slug}/desk  ·  /admin/workspace
```

Use **`/lab`** not **`/desk`** — no three-tab chrome.

### Track A — Shell + UX (**S**) — **shipped 2026-08-28**

| # | Task | Status |
|---|------|--------|
| A1 | **Trader workspace shell** — no three-tab chrome | **Done** — `TraderWorkspace.tsx`. Does **not** reuse `StockBrokerWorkspace`: that mounts Signal + Param Chart hidden to keep their SSE alive, which traders must not pay for on a page where they have not chosen those tools |
| A2 | Instrument-first routes | **Done** — landing `/t/[slug]/workspace`; tool window `/t/[slug]/lab/[instrument]?tool=`; `/t/[slug]/lab` redirects to the landing. Decision #8 resolved to `/workspace` + `/lab` (not `/desk`) |
| A3 | Split **`readOnly`** vs **`automationEnabled`** | **Done** — `OptionsLabPanel` takes both; `automationEnabled` defaults to `!readOnly` so the admin desk is unchanged |
| A4 | Instrument UX | **Done as Option A** (decision #3). Picker at `/lab`, instrument in URL, but the chain still follows tenant config — the panel **says so on screen** when URL ≠ streamed underlying rather than mislabelling the chain |
| A5 | **Books** | **Done — out of SKU v1** (decision #4). `/portfolios/*` stayed admin-only because per-user isolation (Track D) is not done; tenant-shared books would let end users edit each other's. Books + Flows buttons moved **inside** the admin guard, which also fixes the F2 403 |

**Button grouping now mirrors backend contexts exactly** — that is the invariant to
preserve when adding tools:

| Group | Guard | Tools |
|-------|-------|-------|
| Trader (`TraderContext`) | `automationEnabled` | Screener, Heat map, Ideas, Backtest, Bot |
| Admin (`AdminContext`) | `!readOnly` | Flows, Books, Mock, Reset IV, Reset Δ OI, setup bar |

### Track B — API / RBAC (**M**) — **B1 + B2 shipped 2026-08-28**

Contexts live in `apps/backend/src/app/api/options_lab.py`. `TraderContext` carries the
same role set as `ViewerContext` today (there is no distinct trader role) — it is a
separate alias so a future trader role tightens these routes in one place.

| Route group | Context now | Notes |
|-------------|-------------|-------|
| `GET /config`, `GET /chain`, `GET /stream` | `ViewerContext` | Unchanged — stream uses **tenant config** until E1 |
| `PATCH /config`, resets | `AdminContext` | Admin-only; admin pre-sets the tenant instrument in MVP Option A |
| `GET /screener` | **`TraderContext`** | Ideas |
| `/backtests/*` (6 routes) | **`TraderContext`** | Backtest |
| `/bots/*` (7 routes) | **`TraderContext`** | Paper only for end_user — see B2 |
| **`POST /margins`** | **`TraderContext`** | Builder margins (**F3**) |
| **`GET /iv-history`** | **`TraderContext`** | Backtest IV points (**F3**) |
| **`/portfolios/*`** | `AdminContext` | **Books stays admin-only** — decision #4 resolved, see A5 |
| `/orders`, `/gtts`, broker reconcile, `/flows` | `AdminContext` | Stay admin-only |

| # | Task | Status |
|---|------|--------|
| B1 | `TraderContext` on rows above + RBAC tests | **Done** — `tests/test_options_lab_rbac.py` (5 tests) |
| B2 | Paper-only bots for `end_user` | **Done** — see policy below |
| B3 | Document org-bound Kite credentials | Todo |

**B2 policy as implemented** — non-admins may **read** live bots but never create, mutate,
or fire them:

- `POST /bots` with `mode=live` → 403.
- `PATCH /bots/{id}` → 403 when setting `mode=live` **or** when the stored bot is live.
- `DELETE /bots/{id}` and `POST /bots/{id}/run` → 403 when the stored bot is live.
- `run` forces `auto=false` for non-admins — `auto` skips the confirm gate and is a
  worker/admin path only.

Admin capability is checked with `context.can_administer()`, so admin service accounts
keep working.

### Track C — Automation UI (**S–M**)

**C1 Done** — overlay buttons gated by `automationEnabled`. **C2 Done** — same-window
overlays kept; Ideas→Backtest still uses the in-process handoff, so no URL/channel work
was needed. **C3** sub-routes and **C4** cross-window handoff remain optional and are
only worth doing once sub-windows are actually wanted.

### Track D — Data isolation (**M**) — **shipped**

Bots and backtests stay in **one tenant blob** — the bots worker has to read
every armed bot in a single pass — but the blob is now **partitioned by owner**.
Each row carries `owner_id`; a trader sees, edits, deletes and runs only their
own, and another user's row reads as *not found* rather than *forbidden* so ids
cannot be enumerated. Operator scope (`can_administer()`, and the worker's
`tenant_admin` context) still sees the whole tenant.

Rows written before ownership existed carry no `owner_id`. They were all created
while these routes were admin-only, so they stay **operator-only** rather than
becoming shared with every end user. `MAX_BOTS_PER_OWNER` caps a trader inside
the shared blob so one user cannot fill it.

Covered by `tests/test_options_lab_ownership.py`.

### Track E — Stream + watcher (**M**) — **shipped 2026-08-28**

| # | Task | Status |
|---|------|--------|
| E1 | **`?underlying=`** on stream + chain GET; cold path config from query | **Done** — `config_for_underlying()` builds a request-scoped config; fingerprint **pointer** key gained the instrument dimension (the snapshot key already had it via fingerprint, per F4) |
| E2 | **Per-instrument watcher** (replace tenant-singleton `_watchers`) | **Done** — watch key is `{tenant}\|{instrument}`; `list_watched()` now returns `(tenant, wings, instrument)` and the worker warms per instrument |
| E3 | Two windows, two instruments, same tenant — no wrong stream | **Done** — `tests/test_options_lab_instrument_scope.py` (12 tests) |

**The A4 constraint is lifted end to end:** `/lab/{instrument}` drives the real
chain. `getOptionsChain` and `streamOptionsChain` send `?underlying=`, the stream
effect re-opens when the window switches instrument, and a pinned window no
longer gates on `configReady` — the backend derives underlying + FUT from the
query param, so an incomplete tenant desk config cannot block it. The mismatch
banner stays as a safety net and self-disables when the two agree.

**Decision #3 is therefore superseded:** the MVP is Option B, not Option A. One
instrument per *tenant* is no longer a pilot constraint for Options Lab.

**Derived, not inherited:** `config_for_underlying` recomputes FUT via
`suggest_fut_symbol` and takes strike step from presets. Carrying the desk FUT
across would quote NIFTY futures on a SENSEX chain. An equity outside
`EQUITY_FNO_SEED` keeps the desk strike step — the full NFO parse (I2) is the fix.

**Sentinel:** `-` means "whatever the tenant desk config says", so every existing
unpinned caller behaves exactly as before E1.

### MVP exit criteria

1. End user never sees Signal or Param Chart.
2. End user sees chain for **tenant’s configured instrument** (MVP Option A) or URL instrument (Option B + E1).
3. End user can use Ideas, Backtest, Bot (paper), and Books (if in SKU).
4. Builder margins and backtest IV work (B1).
5. Admin desk unchanged; live orders/bots admin-only.
6. **Documented pilot constraint:** one instrument per tenant **unless E1 shipped**.

### Timeline (revised)

| Milestone | Scope | Calendar |
|-----------|-------|----------|
| **MVP (Option A)** | B1 + A1–A3 + C1–C2; one instrument/tenant; no URL stream | **~1–2 weeks** |
| **MVP (Option B)** | Above + E1–E3; real URL instrument | **~3 weeks** |
| **MVP+** | Sub-routes C3 | +3–5 days |
| **Scale** | Per-user stores (D), admin Phase 0 Chart overlay | +1–2 weeks |

---

## Implement now — Admin desk fixes

**Do not start P0 engine merge until open decision #6 is resolved (F6).**

| Priority | Item | Status | Notes |
|----------|------|--------|-------|
| **P−1** | Resolve **#6** `desk_instrument` vs URL authority | **Resolved in code** | Board owns the **desk primary**; the URL owns **per-window rows** (`config_for_instrument`). The two never contend because they scope different things |
| P0 | Signal `_load_config()` / worker merge board | **Done** — `config_with_desk_board()` applies the board in `_load_setup`, so the ticker and matrix builds see what the UI shows. Signal keeps its own auto-rolled CE/PE while the instrument is unchanged | Invalidation contract (F5) shipped with it |
| P0 | Lab preset → `publishDeskInstrument` | Done | |
| P1 | Central `patchIdentity()` | Todo | **S** |
| P1 | Param Chart subscribe guard | Done | |
| P2 | Admin instrument-first landing | Blocked on Chart Phase 0 | **M** |

**P0 invalidation (F5): done.** `options_lab.update_admin_config` now calls `delete_metric(tenant_id, "setup")` whenever the identity patch actually moves the board, matching what Param Chart already did.

---

## Current state

### Three desks, three pipelines (admin)

| Desk | Stream | Config nest |
|------|--------|-------------|
| Signal | `/admin/signals/stream?instrument=` | `signal_engine` |
| Options Lab | `/admin/options-lab/stream?wings=` only | `options_lab` |
| Param Chart | `/admin/param-chart/stream` | `param_chart` |

`desk_instrument` fixes UI drift; does not merge Redis caches or align engine read path.

### Customer desk today

- `StockBrokerWorkspace` + `readOnly=true` — all three tabs.
- Automation hidden; Books clickable → 403 (F2).
- End user: GET config/chain/stream ✅; PATCH + automation ❌.

### Options Lab automation (componentized)

| Sub-tool | Component | Backend |
|----------|-----------|---------|
| Ideas | `OptionsLabIdeasPanel` | `GET /screener` (admin) |
| Backtest | `OptionsLabBacktestPanel` | `/backtests` (admin) |
| Bot | `OptionsLabAutomationsPanel` | `/bots` (admin) |

### Cache gaps (corrected)

| Surface | Instrument in data key? | Stream/request instrument? | Risk |
|---------|-------------------------|------------------------------|------|
| **Signal** | Yes (`row:{key}`) | Yes (`?instrument=`) | Low |
| **Param Chart overlay** | No | No | Clobber |
| **Param Chart month pack** | Partial (read guard) | No | Rebuild thrash |
| **Options Lab snapshot** | **Yes** (`fingerprint` in key) | **No** — tenant config + tenant watcher | Wrong instrument on URL; watcher not per-instrument (**F4**) |

Admin Phase 1 blocked on **Param Chart** overlay. Lab SKU MVP can ship on **one instrument/tenant** without E1.

---

## Lab sub-tools (separate windows)

Bot (**S**) → Ideas (**S–M**) → Backtest URL handoff (**M**). Backtest→Bot via `backtest_id` works cross-window once routes exist.

---

## Product rules

**Admin:** one instrument per window; URL authority TBD (#6).  
**End-user Lab SKU:** one instrument per session; MVP may be one instrument **per tenant** until E1.

---

## Long-term phases

### Phase 0 — Admin prerequisites (**M**) — mostly shipped 2026-08-28

1. **Param Chart overlay instrument dimension — Done (cache half).** Overlay key is
   `…:overlay:{instrument}`; `get_overlay`/`set_overlay`/`overlay_frame_from_cache`/
   `month_state_for_stream` take an optional instrument. **Not done:**
   `?underlying=` on the Chart *stream route*. Chart config carries month packs,
   strike, and CE/PE, so request-scoping it is Phase 1 work, not a key change.
   Reader and writer both use the desk slot today, so behaviour is unchanged.
   Tests: `tests/test_param_chart_overlay_scope.py`.
2. **Lab stream `?underlying=` + per-instrument watcher — Done** (E1–E2 above).
3. Param Chart pack reuse rules — **Todo.** Pack key is
   `…:pack:{interval}:{year}-{month}` with an identity guard on read; two
   instruments still cause rebuild thrash rather than wrong data.

**Exit criterion status:** met for Options Lab (E3 test). **Not yet met for Param
Chart** — needs item 3 plus the Phase 1 stream param.

### Phase 1 — Admin instrument-first UX (**S–M**, after Phase 0)

Landing search · tool per tab · Lab sub-routes.

### Phase 2 — One identity (**M**, in progress)

Board merge on GET done; **`_load_config()` merge + setup invalidation** todo; resolve #6 first.

### Phases 3–5

Unified desk row · views not silos · Redis cleanup — unchanged intent.

---

## Reference

### Supported instruments

Six index presets + ~20 equity seed; full NFO parse available; screener cap 20. Expand universe freely; expand pinned slowly.

### Kite / deployment

One WS hub per tenant · ~60 REST/min · size pilot for Lab SSE count.

#### Single-worker constraint — the SKU's real scaling ceiling (decision #10)

Desk workers run **in-process with the API** (`runtime.py` `DomainServices.start()`), and two
refuse to start when `WEB_CONCURRENCY > 1`:

| Worker | Behavior at `WEB_CONCURRENCY > 1` | Reason in code |
|--------|-----------------------------------|----------------|
| `kite_ticker_hub` | Refuses sync | "~3 WS connections per api_key; each process opens its own" |
| `options_lab_bots_worker` | Refuses start | "refuse multi-process until a leader lock exists" |

So the whole app is pinned to **one process** if you want live ticks or bots. The end-user Lab
SKU sells **paper bots** and **live chain SSE** — both sit behind that constraint, and every
new customer adds an ~8 Hz SSE connection to the same process running four tick workers.

This is not host sizing; it is the ceiling the SKU hits first — **before** the cache gaps.
**Target:** run ticker/bots as their own singleton service (or add the leader lock the bots
comment already anticipates) and let API workers scale horizontally. Cheap to design for now,
expensive to retrofit once customers are on it.

### What we are not doing (now)

- Full desk SPA rewrite.
- End-user live bot auto-fire without policy.
- URL instrument **without E1** while claiming URL drives chain.

---

## Open decisions

| # | Decision | Blocks | Priority |
|---|----------|--------|----------|
| ~~6~~ | **Resolved — both, at different scopes.** The board is authoritative for the **desk primary** (merged in `config_with_desk_board`); the URL is authoritative for a **per-window matrix row** (`config_for_instrument`). Signal keeps its own auto-rolled CE/PE | P0 engine merge (F6) | **Shipped** |
| ~~1~~ | **Resolved** — paper-only v1; non-admins cannot create, mutate, or run live bots | B2 | **Shipped** |
| ~~2~~ | **Resolved — per-user inside the tenant blob.** Rows carry `owner_id`; the worker keeps an unscoped view | D | **Shipped** |
| ~~3~~ | **Resolved — Option B.** Shipped as Option A first (URL cosmetic), then E1–E3 landed and the URL now drives the real chain. No one-instrument-per-tenant constraint for Lab | A4 | **Shipped** |
| ~~4~~ | **Resolved** — Books out of SKU v1; revisit now that Track D per-user stores have shipped | A5 | **Shipped** |
| 10 | **Split desk workers from API processes** — ticker/bots are single-worker; SKU adds SSE per user | Scale ceiling | **Before customer #2** |
| 5 | Pinned universe size | Phase 3 | |
| 7 | Ideas→Backtest handoff mechanism | C | |
| ~~8~~ | **Resolved — `/lab`**; admin desk unchanged at `/chat` → `StockBrokerCustomerDesk` | A2 | **Shipped** |
| 9 | Chain cache vs desk row summary | Phase 3 | |

---

## Related docs

- [Desk navigation map](desk-navigation-map.html) — route tree (note: URL instrument requires E1)
- [Desk instrument board](desk-instrument.md)
- [Architecture — Trading desk](architecture.md#trading-desk)
- [Tech debt](tech-debt.md)
- [Options Lab guide](../Instructions/StockBroker/OPTIONS_LAB_GUIDE.md)
