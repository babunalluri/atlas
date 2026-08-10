# Workflow: Learn via Knowledge Base (End User)

## Trigger

End user asks about concepts, plans, onboarding, risk disclosure, or “how do I learn trading on TradeForge?”

## Actors

- Learning Guide (primary)
- Customer Concierge (handoff when user wants to trade)

## Classplus

**Out of scope to rebuild.** Do not integrate or host LMS. Optional deeplinks only if present in KB.

## Steps

1. **Classify intent** — learning vs trading action. Trading → Concierge.
2. **Retrieve KB** — use Atlas Knowledge attached to the Learning team/agent.
3. **Answer** — grounded in KB; cite topic titles when helpful.
4. **Plan gate** — if KB says content is plan-locked, explain upgrade path from KB (no fake catalog).
5. **Next step** — either another KB topic or hand off: “Ready to paper-trade a signal?” → `paper_from_signal` via Concierge.
6. **Disclosure** — when teaching live/demat, remind SEBI risk disclosure exists in KB / Me screen; do not skip.

## Pass

- Answers match KB; no hallucinated modules.
- Clear handoff to Concierge for UC-1 paper path.

## Fail

- Invented curriculum or Classplus video transcripts.
- Learning agent places Groww/paper orders.
