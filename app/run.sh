#!/usr/bin/env bash
# Start the FastAPI backend and the Streamlit frontend together.
# Run from the app/ directory: bash run.sh

set -e

# Resolve project root (one level above app/)
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$APP_DIR")"

echo "=== Investment RAG System ==="
echo "App dir : $APP_DIR"
echo "Root dir: $ROOT_DIR"
echo ""

# Make sure .env is readable
if [ ! -f "$ROOT_DIR/.env" ]; then
  echo "ERROR: .env not found at $ROOT_DIR/.env"
  exit 1
fi

# Install dependencies if needed
pip install -q --upgrade -r "$APP_DIR/requirements.txt"

# Start FastAPI backend (port 8000) in background
echo "Starting FastAPI backend on http://localhost:8000 ..."
cd "$APP_DIR"
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Give the server a moment to start
sleep 2

# Start Streamlit frontend (port 8501)
echo "Starting Streamlit frontend on http://localhost:8501 ..."
streamlit run "$APP_DIR/frontend/Home.py" --server.port 8501

# If Streamlit exits, kill backend too
kill $BACKEND_PID 2>/dev/null || true
