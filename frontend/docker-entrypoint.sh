#!/bin/sh
# Inject the RUNTIME backend URL into the served bundle, then start Next.
# Set API_BASE (or NEXT_PUBLIC_API_BASE) in the container's env; this writes it to
# /__env.js which the app reads at load time — so one built image works in any environment.
set -e

API_BASE="${API_BASE:-${NEXT_PUBLIC_API_BASE:-}}"
echo "window.__ENV = { API_BASE: \"${API_BASE}\" };" > /app/public/__env.js
echo "Runtime API_BASE = '${API_BASE:-<empty: falling back to build-time/localhost>}'"

exec node_modules/.bin/next start -p "${PORT:-3000}"
