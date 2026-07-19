#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/packages/contracts/openapi.json"
URL="${AGENTOS_URL:-http://localhost:7777}/openapi.json"

echo "Fetching OpenAPI from $URL"
curl -fsSL "$URL" -o "$OUT"
echo "Wrote $OUT"

# Optional: if openapi-typescript is available, emit stronger types.
if command -v npx >/dev/null 2>&1; then
  npx --yes openapi-typescript "$OUT" -o "$ROOT/packages/contracts/src/openapi.ts" || true
fi
