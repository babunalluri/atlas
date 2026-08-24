# Atlas Options Lab — Operator Guide

**Audience:** India F&O desk operators using Atlas Stock Broker  
**Scope:** How Options Lab works, how to start safely, and how to think about risk and P&L  
**Not:** Personalised investment advice. Options can lose more than the premium you pay (especially when selling). Trade only with capital you can afford to lose.

---

## 1. What Options Lab is (and is not)

Atlas Options Lab is your **live India options desk**: chain, OI/IV context, strategy builder, books, backtests, and paper automations — wired to Kite when tools are bound.

| Lab is | Lab is not |
|--------|------------|
| A desk to **see, build, paper, and carefully place** multi-leg books | A guarantee of profit |
| A place to **reconcile** Lab books vs broker positions | A full OMS / fill simulator |
| Paper bots with **gates, SL/TP %, event-avoid, DTE flat** | “Set and forget” live auto-trading (live never auto-fires) |

**Pair Lab with:** Kite (or your broker) statements, GTTs, and your own written playbook.

---

## 2. Options in 10 minutes (India F&O)

### Calls and puts

- **CE (Call):** right to **buy** the underlying at the strike. Buyers usually want the market **up**.
- **PE (Put):** right to **sell** at the strike. Buyers usually want the market **down**.
- You can **buy** (debit / limited loss ≈ premium) or **sell** (credit / loss can be large — always define risk).

### Premium, lots, and expiry

- **Premium** = option price (₹ per share). P&L ≈ change in premium × quantity × direction.
- India index options trade in **lots** (e.g. NIFTY lot size can be 75). Lab Builder qty is usually **lots**; Kite positions are **shares** (lots × lot size). Reconcile compares in **lots**.
- **Expiry:** weekly / monthly. Time decay (**theta**) hurts long premium and helps short premium — faster near expiry.

### Greeks (desk shorthand)

| Greek | Rough meaning |
|-------|----------------|
| **Delta** | How much premium moves if spot moves ₹1 |
| **Gamma** | How fast delta changes (risk near ATM / expiry) |
| **Theta** | Daily time decay |
| **Vega** | Sensitivity to implied volatility (IV) |
| **IV / IVP** | How expensive options are vs history (IVP high → rich) |

You do **not** need perfect Greek math to start — you **do** need a defined view (direction / range / vol) and a defined exit.

### Strategies Lab can build

Templates include long/short singles, straddles/strangles, vertical spreads, iron condor / butterfly, ratios. **Call calendar** stays gated until dual-expiry chain exists.

---

## 3. Before you open the Lab

### Checklist

1. **Kite session** valid (access token renews ~daily after login).
2. **Signals ops / Live** teams published with Kite tools bound (`get_quote`, `get_positions`, margins, and for live: `place_order`, GTT tools as needed — see `tools/KITE.md`).
3. **Lab setup:** underlying (e.g. NIFTY 50), **FUT symbol** for the chain month, strike step, **Mock feed off** for real data.
4. **Compose flags** (ops): ticker / bots only if you intend them (`OPTIONS_LAB_BOTS_ENABLED`, etc.).
5. Written **rules:** max loss/day, max lots, no revenge trade, no size-up after a loss.

### Where to click

Stock Broker workspace → desk tabs:

1. **Signal Engine** — checklist / macro gates (optional but useful before entries).
2. **Options Lab** — chain + builder + Ideas / Backtest / Books.
3. **Automations** — paper bots + **Reconcile**.

---

## 4. Step-by-step: first session (paper)

### Step A — Setup bar

1. Open **Options Lab**.
2. Pick preset (**NIFTY / BANKNIFTY / …**).
3. Confirm **FUT** matches the expiry you want.
4. Confirm **Mock** is off for live quotes.
5. Wait until Spot / ATM / PCR-style strip populates.

### Step B — Read the market (Quotes | OI | Straddle | IV)

Stay on the left shell — do not skip this.

| Tab | Use it to ask |
|-----|----------------|
| **Quotes** | Where is ATM? Are CE/PE liquid? Spreads wide? |
| **OI** | Where are walls / build-up? Reset Δ OI baseline if needed. |
| **Straddle** | Is ATM straddle rich or melting? |
| **IV** | Is IV elevated (IVP)? Selling rich vs buying cheap? |

**Rule of thumb:** If you cannot explain *why* the trade fits the day in one sentence, do not build legs yet.

### Step C — Build a strategy

1. Open the strategy rail (Builder).
2. Start with a **defined-risk** template (e.g. bull put / bear call spread or iron condor with width you understand) — not naked shorts on day one.
3. Check **payoff**, net premium, rough delta.
4. Check **margin** (basket / available) before any place.
5. Prefer **paper** place first.

### Step D — Ideas → Backtest (optional)

1. **Ideas** — screen candidates; send one to Backtest.
2. **Backtest** — run model / hist / BS marks; treat results as **scenario**, not prophecy.
3. Save a run you understand; optionally **Create bot** from it (paper, event-avoid on, flat ≤ 1 DTE by default).

### Step E — Books

1. Save drafts from Builder, or **Import from Kite**.
2. Watch **MTM**; use GTT list/cancel when tools are bound.
3. Hit **Reconcile** — expect qty in **lots**; Kite import books show qty in **shares**.

### Step F — Automations (paper only until proven)

1. Add / arm a **paper** bot with schedule, entry gates (IVP/PCR/DTE), SL % / TP %, event-avoid, max DTE hold.
2. **Evaluate now** or wait for worker when `OPTIONS_LAB_BOTS_ENABLED` is on.
3. **Live never auto-fires** — live **Run once** is HITL (`confirm`).
4. Use **Reconcile** for broker cash / used margin vs Lab OPEN / books.

---

## 5. How to start trading (progression)

```text
Mock / learning  →  Paper books & bots  →  Tiny live (1 lot defined risk)
                 →  Scale only after a boring, rule-following streak
```

1. **Week 1–2:** Paper only. Same setup every day. Log why you entered/exited.
2. **First live:** One defined-risk spread, 1 lot, pre-set SL/target or GTT, max daily loss hard stop.
3. **Never** jump from a winning paper day to max size live.
4. Keep **Reconcile** green (or understood) before adding size.

---

## 6. How to be careful on the market

### Session hygiene

- Prefer liquid underlyings (NIFTY/BANKNIFTY) and liquid strikes.
- Avoid forcing trades in the first noisy minutes unless that is *your* tested edge.
- Respect **event-avoid** (NSE holidays, FOMC days) — bots skip new entries; you should too unless the playbook says otherwise.
- Wide bid–ask + low OI = pay the market maker; size down or skip.

### Position hygiene

- Know **max loss** before click (especially short premium).
- One thesis per book; do not stack correlated shorts “because IV is high.”
- Flatten or reduce into **expiry week** unless you explicitly trade pin/assignment risk.
- After a stop-out: **stop for the day** if that is your rule — Lab kill switch / disarm exists for a reason.

### Ops hygiene

- Mock off for real decisions.
- Token / toolkit failures → no guessing fills.
- Margin squeeze: watch **used** and util % on Reconcile, not only available cash.

---

## 7. How to avoid large losses

Pros survive by **cutting left-tail risk**, not by predicting every move.

| Practice | Why |
|----------|-----|
| Defined risk (spreads / condors with wings) | Caps disaster vs naked short |
| Hard daily loss limit | Stops revenge trading |
| Size from risk, not from “feeling lucky” | 1 bad day ≠ account damage |
| No averaging losers without a new thesis | Often doubles the mistake |
| Respect IV crush / spike | Long premium dies on crush; short premium dies on spike |
| Exit plan before entry | SL %, target %, time stop, or DTE flat |
| Reconcile Lab vs broker | Stops “ghost” books and wrong lot math |

**Selling options:** credit looks easy until one gap. Always know margin and wing distance.

**Buying options:** limited loss, but high chance of 100% premium loss. Buy when you have a catalyst *and* a time box.

---

## 8. How to approach profits (realistic)

Profits come from **repeatable edge + risk control**, not from one heroic trade.

### What “edge” looks like in Lab terms

1. **Context** — Signal checklist + OI/IV/straddle agree with a clear bias (trend, range, or vol).
2. **Structure** — Template matches the bias (e.g. range → iron condor; bullish credit → bull put).
3. **Price** — You are not paying silly mid with no liquidity.
4. **Exit** — Take profit early enough that theta/gamma does not erase the win; cut losers per plan.
5. **Review** — Books MTM + journal: what worked, what was luck.

### Practical profit habits

- Target **process wins** (followed rules) over P&amp;L bragging.
- Prefer many small, planned outcomes over rare lottery tickets.
- Scale **after** drawdowns are controlled — never during emotional highs.
- Use Backtest as a **sanity check**, not a green light to oversize.

### What usually destroys P&amp;L

- Overtrading after a win or loss  
- Ignoring event days / holidays  
- Naked short “income” without wings  
- Holding losers into expiry “for a bounce”  
- Treating paper bot wins as proof of live edge  

---

## 9. Daily operator loop (printable)

**Pre-open**

- [ ] Kite session OK  
- [ ] Lab FUT + mock correct  
- [ ] Signal Engine: any hard NO-GO?  
- [ ] Know max loss for the day  

**During**

- [ ] Quotes → OI → Straddle/IV before Builder  
- [ ] Margin check → paper or small live  
- [ ] GTT / SL-TP attached when live  
- [ ] Reconcile after fills  

**Post**

- [ ] Flat or sized for overnight intentionally  
- [ ] Disarm bots if you leave the desk  
- [ ] One-line journal: thesis / result / lesson  

---

## 10. Lab map (quick reference)

| Surface | Job |
|---------|-----|
| Setup bar | Underlying, FUT, strike step, mock |
| Quotes / OI / Straddle / IV | Market context |
| Builder + payoff | Construct & sanity-check risk |
| Ideas | Candidate structures |
| Backtest | Scenario / hist / BS marks |
| Books | Draft / paper books, MTM, GTT, Reconcile |
| Automations | Paper bots, Evaluate, Reconcile, event-avoid / DTE flat |

**Broker truth:** Kite positions & margins remain authoritative. Lab Reconcile is the bridge — it does not replace the broker app.

---

## 11. Related Atlas docs

- `tools/KITE.md` — toolkit bind list (quotes, margins, GTT, orders)  
- `tools/SIGNAL_ENGINE.md` — checklist feeds & ticker flags  
- `teams/signals_ops.md` — Signals ops team  
- `workflows/kill_switch.md` — emergency stop patterns  
- `workflows/paper_from_signal.md` — paper path from signals  

---

## 12. Closing rule

**Survive first. Size second. Edge third.**

Options Lab gives you visibility and discipline hooks. Your playbook decides whether those tools make money. When unsure: paper, smaller, or flat.
