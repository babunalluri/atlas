FROM node:22-alpine AS base
WORKDIR /app
RUN apk add --no-cache libc6-compat

FROM base AS deps
COPY package.json ./
RUN npm install

FROM base AS development
ENV NODE_ENV=development
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

FROM base AS builder
ENV NEXT_TELEMETRY_DISABLED=1
# NEXT_PUBLIC_* are inlined into the client bundle at build time, so a
# production image must be built with the deploy's real URLs — runtime
# `environment:` entries are ignored once `npm run build` has run.
ARG NEXT_PUBLIC_AGENTOS_URL
ARG NEXT_PUBLIC_APP_URL
ARG NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER
ARG NEXT_PUBLIC_DEV_AUTH="false"
ARG NEXT_PUBLIC_USE_MOCKS="false"
ENV NEXT_PUBLIC_AGENTOS_URL=$NEXT_PUBLIC_AGENTOS_URL \
    NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL \
    NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER=$NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER \
    NEXT_PUBLIC_DEV_AUTH=$NEXT_PUBLIC_DEV_AUTH \
    NEXT_PUBLIC_USE_MOCKS=$NEXT_PUBLIC_USE_MOCKS
# Build context is only `apps/web` (WORKDIR /app). Pin tracing to the app dir
# so standalone emits `/app/.next/standalone/server.js` — matching the
# production stage `COPY .next/standalone ./` + `CMD ["node", "server.js"]`.
# Without this, default monorepo root nesting puts server.js under
# `standalone/apps/web/` and the web container crash-loops.
ENV NEXT_OUTPUT_TRACING_ROOT=/app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM base AS production
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
