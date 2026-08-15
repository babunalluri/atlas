# Research toolkit — Atlas setup

Source: `tools/research_toolkit.py`

Compute-only strategy math for the **Research** desk team. It does **not** call
Groww, Kite, or any broker. Live prints come from whichever vendor toolkit is
assigned on Research (`get_ltp` / `get_quote` / `get_ohlc`).

This is **not** Sensibull / Tradetron / OpenAlgo. No option-chain UI, no
strategy builder, no live order routing.

## Load into Atlas

1. Tool Builder → Editable Python → paste `research_toolkit.py`.
2. No host allowlist and no credentials — there is no HTTP client.
3. Validate → Publish. Do **not** mark any capability mutating.
4. Attach to the **Research** team (and the Researcher agent). Optionally attach
   the tenant’s broker toolkit on the same team for **read-only** quotes
   (`get_ltp`, `get_quote`, `get_ohlc`). Do not auto-bind Groww.
5. Never attach `place_order` / `modify_order` / `cancel_order` for Research use.

## Capabilities

| Tool | Inputs | Purpose |
|---|---|---|
| `research_stock_snapshot` | symbol + last/OHLC and optional close/high/low series | Trend, SMA, momentum, crude S/R |
| `research_compare_symbols` | two symbols + LTPs (optional % change) | Relative snapshot |
| `research_option_payoff` | defined structure + strikes/LTPs | Payoff, breakeven, max loss / max profit |

Defined F&O structures only: `long_call`, `long_put`, `covered_call`,
`bull_call_spread`, `iron_condor`.

## Agent usage notes

- You MUST fetch or collect every price before calling these tools.
- If quote tools fail or return nothing, say so. Never invent LTP, IV, Greeks, or a chain.
- Groww/Kite quote APIs can price a **known** FUT/OPT symbol. They do not list a live option chain in this pack — accept strikes as inputs and compute math only.
- Historical candles are not in the Groww/Kite adapters. Without a close series, SMA/S-R is limited to the current bar.
- Hand live orders to **Live trading** (HITL). Research never places, modifies, or cancels.
