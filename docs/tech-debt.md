# Tech debt (defer)

Tracked work we are **not** doing now. Pick up later when prioritizing UX/perf or platform polish.

## Web: match “fast portal” feel (Next.js vs Vite SPA)

**Context (2026-08-27):** Another internal portal feels extremely fast. It is a **Vite React SPA** (Ant Design + Tailwind): after first load, navigation is client-only; assets are content-hashed, gzipped (~3.5 MB → ~1.1 MB), HTTP/2, aggressively cached (reload DOMContentLoaded ~200 ms). Cold first visit still pays for a single large JS chunk (little route splitting).

**Atlas today (`apps/web`):** **Next.js 15 App Router** + Tailwind + custom UI (not Ant Design). Vite is used for Vitest only. Soft navigation via Next `Link` can feel SPA-like after hydrate, but the model is SSR/RSC + per-route payloads, plus next-auth / next-intl / Monaco / markdown weight. Pilot perceived lag has often been **backend/SSE/sandbox**, not only JS framework.

**Do later (highest leverage first):**

1. **Route-based code splitting / lazy panels** — `next/dynamic` for heavy admin and desk surfaces (Monaco, large charts, rarely used Lab remnants) so cold first paint is not dominated by one fat client graph.
2. **Static asset caching** — confirm production/`/_next/static` long-cache headers + gzip/brotli on the pilot/edge (CDN optional).
3. **Desk paint path** — keep Tier-A on ticker/cache; finish proving Tier-B blanks (OI / PCR / IV / crude / USD) refill under market hours without sandbox thrash (related Signal work already partially shipped).
4. **Do not** rewrite the whole app to a Vite SPA unless product explicitly wants that architecture; treat SPA-like snappiness as an optimization goal on Next first.

**Out of scope for this debt item:** converting Atlas to Vite + Ant Design; IVP/Dow feeds (manual by design).

## Signal / pilot (related, also deferred polish)

- Soft-pinned preset switch without cold epoch when possible.
- Sticky UI `SAVING…` / multi-admin config PATCH fights.
- ADX / metric sanity when history is thin or mis-bucketed.
- Host sizing (2 OCPU) vs further matrix/chain fan-out trim after sandbox is near-zero for Signal.
- Confirm Options Lab / param-chart / chat are not still driving `/v1/runs` when the trading desk alone is open.
