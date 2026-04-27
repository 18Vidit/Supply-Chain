#!/bin/bash
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip install -r requirements.txt --break-system-packages -q
echo "Starting Route Risk Analysis System..."
echo "Frontend: open frontend/index.html in your browser"
echo "API docs: http://127.0.0.1:8000/docs"
cd backend
PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
