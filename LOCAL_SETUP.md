# Run PlanPerm locally

This project now runs as a **Streamlit** application. It queries public Irish planning records from MyPlan.ie’s ArcGIS service and opens source records in a new browser tab.

## Requirements

Install **Python 3.11+**. No Node.js, database, file storage, or API key is required for the current interface.

## Start the app

```bash
cd planperm-ui-refresh
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL shown by Streamlit, normally `http://localhost:8501`.

## What works

| Function | Behaviour |
|---|---|
| Search | Finds Irish places, Eircodes, and addresses through Nominatim. |
| Pin | Click the map to move the selected site and refresh nearby applications. |
| Radius | Select 0.5–5 km; the query and markers update automatically. |
| Application points | Click a point to read its record and open its original planning source. |
| Assistant | A normal chat layout is ready for a later PlanPerm AI connection. |

## Notes

The app uses live public data. Search and planning results can vary as the public source changes. The assistant is intentionally only a UI placeholder until its AI service is connected.

During local development, Streamlit may log a `WebSocketClosedError` when a browser tab or automated preview disconnects while a page is reloading. This is a browser-session event rather than an application startup failure; refresh the page if it occurs.
