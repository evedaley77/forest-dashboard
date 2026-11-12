# forest-dashboard

CNN User Journeys Regression Testing - Automation Dashboard (Streamlit).

Reads Forest2 result exports (`Forest2_Results*.csv`) and shows KPIs, runs per day, pass rate by
workflow, a bar chart of test names within each artifact name, and run tables. Filter by date range,
test name, artifact name, workflow, brand, platform and status. Import another export from the
sidebar or pick one from a folder.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run streamlit_app.py
```

Exports contain internal test data: do not commit them (`imports/` and `.cache/` are git-ignored).

Created using Anthropic Claude. Keep on internal versions until a human has reviewed and verified the content.
