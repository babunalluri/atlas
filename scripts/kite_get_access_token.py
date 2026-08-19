#!/usr/bin/env python3
"""Get today's Zerodha Kite access_token for Atlas tool credentials.

Same /session/token exchange as Instructions/StockBroker/tools/kite_toolkit.py
(create_session). Paste the printed access_token into Tool Builder credentials.

Usage:
  1. Fill in API_KEY and API_SECRET below (once).
  2. python3 scripts/kite_get_access_token.py
  3. Log in when the browser opens; paste request_token when prompted.

Prerequisites:
  - Kite Connect app at https://developers.kite.trade
  - Redirect URL on the app (e.g. http://127.0.0.1)

Do not commit this file with real api_secret filled in.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# --- Paste your Kite Connect app credentials here (static; not daily) ---
API_KEY = "08yqb0fikw055zay"
API_SECRET = "qpvs9y9ib96d3r95th5uzol6sih9tl0n"
# ------------------------------------------------------------------------

LOGIN_BASE = "https://kite.zerodha.com/connect/login"
TOKEN_URL = "https://api.kite.trade/session/token"
KITE_VERSION = "3"
#dg99LQpO36dYvvQKOGqgt2LaN4mZ8n4L
def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    raw = f"{api_key}{request_token}{api_secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def login_url(api_key: str) -> str:
    return f"{LOGIN_BASE}?v=3&api_key={urllib.parse.quote(api_key)}"


def exchange_token(api_key: str, api_secret: str, request_token: str) -> dict:
    payload = urllib.parse.urlencode(
        {
            "api_key": api_key,
            "request_token": request_token.strip(),
            "checksum": checksum(api_key, request_token.strip(), api_secret),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Kite-Version": KITE_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    if body.get("status") == "error":
        raise RuntimeError(body.get("message") or body.get("error_type") or str(body))

    data = body.get("data")
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError(f"Unexpected response: {body}")

    return data


def main() -> int:
    api_key = API_KEY.strip()
    api_secret = API_SECRET.strip()
    if not api_key or not api_secret:
        print(
            "Set API_KEY and API_SECRET at the top of this script, then run again.",
            file=sys.stderr,
        )
        return 1

    url = login_url(api_key)
    print("\n1. Open this URL and log in to Zerodha:\n")
    print(url)
    print("\n2. After login, copy request_token from the browser redirect URL.")
    print("   Example: http://127.0.0.1/?request_token=XXXX&status=success\n")

    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except OSError:
            pass

    request_token = input("Paste request_token here: ").strip()
    if not request_token:
        print("request_token is required.", file=sys.stderr)
        return 1

    try:
        session = exchange_token(api_key, api_secret, request_token)
    except Exception as exc:
        print(f"\nAuthentication failed: {exc}", file=sys.stderr)
        return 1

    access_token = session["access_token"]
    print("\n--- Success ---")
    print(f"access_token: {access_token}")
    print(f"user_id:      {session.get('user_id', '')}")
    print("\nPaste into Atlas Kite tool credentials:")
    print(json.dumps({"access_token": access_token}, indent=2))
    print("\nToken expires around 06:00 IST tomorrow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
