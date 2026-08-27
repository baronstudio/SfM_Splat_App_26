#!/bin/bash
source .venv/bin/activate
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload &
cd frontend && npm run dev &
sleep 3
xdg-open http://localhost:5173 2>/dev/null || open http://localhost:5173
