# PlanPerm — Planning Permission Intelligence Agent

Drop any location in Ireland → see planning application patterns → ask an AI agent about your chances.

Uses real data from MyPlan.ie (ArcGIS API). No scraping needed.

## Quick start

```bash
# 1. Clone / unzip
cd PlanPerm/PoC

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Enable AI agent — copy .env.example to .env and add your key
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-your-key-here

# 5. Run
streamlit run app.py
```

The app works without an API key (basic keyword mode). Set `OPENAI_API_KEY` to enable the LLM-powered agent.

## What it does

1. Enter any Irish location (Rathmines, Athlone, Cork, etc.)
2. Fetches real planning applications from MyPlan.ie
3. Shows colour-coded map (green=granted, red=refused, yellow=pending)
4. Displays stats: approval rate, breakdown by type, timeline trends, appeal outcomes
5. Chat with the AI agent: "What are my chances for a rear extension?"

## Files

| File | Purpose |
|------|---------|
| `app.py` | Streamlit UI — map, stats, charts, chat |
| `agent.py` | LLM agent with function-calling tools |
| `planning_api.py` | ArcGIS data layer with pagination + caching |
| `analysis.py` | Stats, timelines, appeals, precedent matching |
| `geocoder.py` | Nominatim geocoding + offline fallback |
| `config.py` | API keys, URLs, defaults |
| `mock_data.py` | Mock data generator (fallback if API is down) |

## Requirements

- Python 3.10+
- Internet access (for MyPlan.ie API and Nominatim)
- OpenAI API key (optional, for AI agent mode)
