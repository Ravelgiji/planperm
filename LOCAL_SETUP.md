# Run PlanPerm locally

A **Streamlit** application over public Irish planning data. It reads records
from MyPlan.ie's ArcGIS service, resolves the planning authority for a chosen
site, and monitors that authority's official weekly lists for newly published
applications.

## Requirements

**Python 3.11+**. No Node.js, database or file storage. An OpenAI API key is
optional — every agent falls back to deterministic output without one.

## Start the app

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open the URL Streamlit prints, normally `http://localhost:8501`.

## Configuration

```bash
cp .env.example .env            # then add your key
```

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Enables the agents. Without it the app still runs. |
| `PLANPERM_LLM_MODEL` | Defaults to `gpt-4.1-mini`. |
| `PLANPERM_LLM_BASE_URL` | Optional OpenAI-compatible endpoint. |

Real environment variables take precedence over `.env`, so platform secrets
work unchanged when deployed.

**On rate limits:** OpenAI's request allowance is **per model**. If one model
returns 429, switching `PLANPERM_LLM_MODEL` is the quickest way out, because
the next model has its own allowance.

## What works

| Function | Behaviour |
|---|---|
| Search | Irish places, Eircodes and addresses, via Nominatim. |
| Pin | Click the map to move the site; nearby records refresh. |
| Radius | 0.5–5 km. The record count is counted server-side, so it is exact even where the record list is capped. |
| Records | Every application near the pin, nearest first, filterable by decision and searchable. |
| Assistant | Routes each question to the advisor, draft reviewer or watch agent, and names which one answered. |
| Monitor change | Compares the authority's weekly-list pages against a stored baseline, reads any newly published list of received applications, and estimates each observation deadline. |

## How the watch agent behaves

- **Scans only when you press the button.** Nothing runs in the background and
  nothing is emailed.
- **Watched locations stay on the machine**, under `watch_data/`, which is
  gitignored. A fresh clone or deployment starts with no watched areas —
  capture a baseline once and later scans compare against it.
- **The first scan reports nothing.** It records the comparison point. Councils
  publish weekly, so the next change is usually days away.
- **Detection is a comparison, not a judgement.** "This document is new" is a
  set difference against the stored snapshot, and each observation deadline is
  five weeks counted from the receipt date printed in the published list. The
  model reads documents and writes the summary; it never calculates a date.
  Every deadline is labelled an estimate and links its source document.

## Notes

The app uses live public data, so results change as the public source does.

Streamlit may log a `WebSocketClosedError` when a tab disconnects during a
reload. That is a browser-session event, not a startup failure; refresh.
