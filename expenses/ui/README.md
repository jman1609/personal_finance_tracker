# Expense Tracker Dashboard

Simple Streamlit dashboard to visualize your expenses.

## Quick Start

```bash
cd /path/to/personal_finance_tracker
streamlit run expenses/ui/dashboard.py
```

The dashboard will open at `http://localhost:8501`

## Features

- **Category Breakdown** — Pie chart and bar chart of spending by category
- **Monthly Trends** — Line chart showing spending over time
- **Top Merchants** — Bar chart of top 15 merchants by spend
- **Transaction Details** — Filterable table of all transactions
- **Filters** — Filter by date range and category

## Requirements

- `streamlit` — Web UI framework
- `plotly` — Interactive charts
- `pandas` — Data manipulation

Install: `pip install streamlit plotly`
