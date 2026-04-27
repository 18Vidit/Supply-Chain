# AxisIQ Global Logistics Control Tower

FastAPI + static Leaflet dashboard for global supply-chain risk, condition-aware routing, and AI decision support.

## What Changed

- Modern SaaS dashboard UI with KPI cards, global map, shipment tables, lane analytics, and AI decision briefs.
- Global operating network with countries, ports, import/export lanes, customs buffers, service levels, costs, and reliability.
- Live condition providers:
  - Open-Meteo weather forecast/current conditions.
  - OSRM route distance and duration.
  - Optional TomTom Traffic Flow when `TOMTOM_API_KEY` is configured.
  - NASA EONET and USGS hazard feeds.
- Intelligent routing with weighted duration, cost, risk, reliability, weather, traffic, and disruption penalties.
- AI decision module with offline rule-based intelligence and optional Gemini support.
- Optional Firebase Realtime Database pushes for alerts and route decisions.
- Global SQLAlchemy models and Alembic migration for countries, ports, lanes, shipments, and route plans.

## Folder Structure

```text
updated_project/
  backend/
    app/
      api/
      crud/
        db_models.py
      domain/
        global_network.py
      firebase/
        realtime_db.py
      models/
      routing/
        route_optimizer.py
      services/
        ai_engine.py
        condition_providers.py
        hazard_poller.py
        optimization_engine.py
      simulator/
        truck_simulator.py
      main.py
    alembic/
      versions/
        363860405da4_initial_tables_v2.py
        9b2a6df0_global_logistics_tables.py
    tests/
    requirements.txt
  frontend/
    index.html
    app.js
    styles/
      styles.css
  .env.example
  requirements.txt
```

## Setup

```powershell
cd C:\Users\saksham\Documents\Codex\2026-04-28\files-mentioned-by-the-user-files\project\files-mentioned-by-the-user-updated\updated_project
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Optional `.env` values:

```text
AI_PROVIDER=gemini
GEMINI_API_KEY=...
TOMTOM_API_KEY=...
FIREBASE_DATABASE_URL=...
FIREBASE_CREDENTIALS_PATH=...
```

## Run

```powershell
cd backend
$env:PYTHONPATH='.'
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`.

## Verify

```powershell
pytest backend/tests
```
