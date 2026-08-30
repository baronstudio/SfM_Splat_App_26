#!/bin/bash
# BIND_HOST=0.0.0.0 exposes the app to the local network (staging box); pass
# 127.0.0.1 as the first argument for a private run. Only UI_PORT has to be
# reachable from another machine — the page talks to its own origin and Vite
# proxies /api, /static and /ws to the backend on the loopback.
BIND_HOST="${1:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-5173}"

source .venv/bin/activate
uvicorn backend.main:app --host "$BIND_HOST" --port "$API_PORT" --reload &
(cd frontend && npm run dev -- --host "$BIND_HOST" --port "$UI_PORT" --strictPort) &
sleep 3
xdg-open "http://localhost:$UI_PORT" 2>/dev/null || open "http://localhost:$UI_PORT"
