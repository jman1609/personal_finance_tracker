# Personal Finance Tracker (AI Expense Categorizer)

This project reads bank/credit-card transactions from CSV/XLSX, normalizes transactions, applies keyword mapping, and uses OpenAI for uncategorized rows.

## Safe project structure

- `src/categorize_expenses.py` — main script
- `config/category_mapping.json` — editable keyword map
- `data/raw/` — private statements (**gitignored**)
- `data/processed/` — generated outputs (**gitignored**)
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
python src/categorize_expenses.py \
  --input "/absolute/path/to/statement.xlsx" \
  --sheet "Sheet 1" \
  --output "data/processed/categorized_transactions.xlsx" \
  --summary "data/processed/category_summary.xlsx"
```

### Run without AI (mapping only)

```bash
python src/categorize_expenses.py --input "/absolute/path/to/file.xlsx" --no-ai
```
