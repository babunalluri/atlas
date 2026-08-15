# Workflow: Learn via Knowledge Base + market questions

## Trigger

End user asks about concepts, plans, risk, **or** a generic ticker question such as “Can you predict TCS for the next few hours/days?”

## Actors

- Learning Guide (Learning window)

## Classplus

**Out of scope.** Do not integrate or host LMS.

## Steps — lessons

1. **Classify** — lesson vs ticker vs paper vs live vs research. Paper → Paper trading. Live/demat → Live trading. Trend/payoff/what-if with numbers → Research. Ticker / “predict …” stays here.
2. **Retrieve KB** — attached Knowledge for how-to / policy.
3. **Answer** — grounded in KB; cite topic titles when helpful.
4. **Plan gate** — if KB says content is plan-locked, explain upgrade from KB.

## Steps — ticker / “predict” questions

1. State you **cannot predict** a future price (not investment advice).
2. Call assigned read-only quotes if bound: `get_ltp` / `get_quote` / `get_ohlc` (vendor aliases OK).
3. Report tool numbers only. Educational reading of the range is OK; targets are not.
4. If no quote tool: say so and point at the TradingView chart on the desk.
5. Optional CTA: paper-trade a thesis in **Paper trading**.

## Pass

- Lessons match KB.
- Prediction asks never invent a target; quotes come from tools or are declined.

## Fail

- Invented curriculum, ticks, or “TCS will be ₹X by Friday.”
- Learning agent places paper or live orders.
