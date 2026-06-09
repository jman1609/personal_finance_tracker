# Personal Finance Tracker (AI Expense Categorizer)

This project reads bank/credit-card transactions from **XLS/XLSX** statements, normalizes transactions, applies keyword mapping, and produces clean outputs for analysis.

> Upcoming: optional OpenAI-assisted categorization for uncategorized rows.

## Safe project structure

- `expenses/src/categorize_expenses.py` — expense categorization script
- `expenses/config/category_mapping.json` — editable expense keyword map
- `expenses/data/raw/` — private expense / credit-card statements
- `investments/` — investment holdings, mutual fund, and stock files
- `expenses/data/processed/` — generated outputs (**gitignored**)
- `.env` — API key (**gitignored**)

## Setup

```bash
cd /Users/jaymangal/Desktop/personal_finance_tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# then edit .env and add OPENAI_API_KEY
```

## Run

```bash
python expenses/src/categorize_expenses.py \
  --input "/absolute/path/to/statement.xlsx" \
  --sheet "Sheet 1" \
  --output "expenses/data/processed/categorized_transactions.xlsx" \
  --summary "expenses/data/processed/category_summary.xlsx"
```

### Run on a folder (recommended)

This will ingest all matching statement files in `expenses/data/raw/`, update a local cumulative
master ledger (gitignored), de-dupe, and rebuild outputs from the master ledger.

```bash
./.venv/bin/python expenses/src/categorize_expenses.py \
  --input-dir expenses/data/raw \
  --sheet "Sheet 1" \
  --master-ledger expenses/data/processed/master_ledger.csv
```

### Run without AI (mapping only)

OpenAI support is not wired yet, so the current script runs mapping-only by default.

> Planned: a `--no-ai` flag once the OpenAI pass is added.
