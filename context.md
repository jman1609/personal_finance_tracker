# Project Context: Personal Finance Tracker

## Goal
Build a Git-ready Python project that reads bank/credit-card statements (CSV/XLS/XLSX), auto-categorizes transactions, and produces clean outputs for analysis.

## Current Direction
- Current implementation: **mapping-first** categorization using a persistent editable mapping file (`config/category_mapping.json`).
- Upcoming: an optional **OpenAI-assisted** pass for uncategorized rows.

## Expected Inputs
- CSV / XLS / XLSX transaction files.
- Common columns seen so far:
  - `Date`
  - `Narration` or `Description`
  - `Amount` OR (`Withdrawal Amt.` + `Deposit Amt.`)

### Bank-specific input notes (HDFC `.xls`)
- Real-world HDFC exports can be **legacy `.xls` (Excel CDFV2)** and require `xlrd`.
- The sheet includes a long account header + a transactions table + a footer.
- Transactions table headers observed:
  - `Date`, `Narration`, `Chq./Ref.No.`, `Value Dt`, `Withdrawal Amt.`, `Deposit Amt.`, `Closing Balance`
- Parsing approach:
  - Find header row by searching for the above column names
  - Skip separator line (`********`)
  - Keep rows where the first cell can be parsed as a **date** (robust to format variations)
  - Keep only one date column in output (`Date`); treat `Value Dt` as optional/ignored by default

## Expected Outputs
- Categorized transactions workbook
- Category summary workbook (month/category totals)
- Review queue for low-confidence or uncategorized rows

## Repo Conventions
- Code in `src/`
- Config in `config/`
- Sensitive raw files in `data/raw/` (gitignored)
- Generated files in `data/processed/` (gitignored)
- API key in `.env` (gitignored)

## Current script status
- `src/categorize_expenses.py` exists and currently supports:
  - HDFC-style parsing (table detection + footer filtering)
  - Statement metadata extraction: `AccountHolderRaw`, `AccountNumber`, `AccountLast4`, `DownloadedOn`, `SourceFile`
  - Multi-file ingestion from `data/raw/` and cumulative de-duped master ledger
  - Mapping-first categorization via `config/category_mapping.json`
  - Basic narration-derived fields: `PaymentMode`, `CounterpartyGuess`, `UPIHandle`, `TxnIdGuess`
  - Review/QA fields: `Flow` (inflow/outflow/neutral), `NeedsReview` + `ReviewReason`
  - Reversal tagging (heuristic): `Tag=REVERSAL_CANDIDATE`, `ReversalGroupId` (paired transactions)
  - Output: `transactions`, `review_queue`, `reversal_candidates`, `qa_summary` sheets and a category summary file

## Categorization notes
- Primary approach: **mapping-first** keyword rules (case-insensitive contains match on narration)
- Keep a simple `Flow`/direction field (inflow/outflow/neutral) to avoid data loss
- Reversals/refunds are common: often appear as separate rows with opposite-sign amounts and similar narration/merchant.
  - Plan: add deterministic reversal pairing/grouping so summaries don’t double-count while preserving raw rows.

## Validation loop
- First run will likely have low coverage until mappings grow.
- Use `qa_summary` (coverage %) + `review_queue` (rows needing mapping) to iteratively expand `config/category_mapping.json` and re-run.

## Master ledger / de-dupe
- Each run can ingest all files in `data/raw/` and update a cumulative master ledger (gitignored): `data/processed/master_ledger.csv`.
- De-dupe strategy (implemented):
  - A stable key built from: `AccountLast4`, `Date`, `Narration`, `RefNo`, `WithdrawalAmt`, `DepositAmt`, `ClosingBalance`
  - Statement metadata like `SourceFile` / `DownloadedOn` is intentionally excluded so re-downloading the same statement doesn't create duplicates.

## Security / Git Hygiene
Never commit:
- real bank/credit-card statements
- `.env`
- generated outputs
- local virtual environments

## Near-term Tasks
1. Add `src/categorize_expenses.py`
2. Support mapping-first + optional AI pass
3. Write outputs and summary files
4. Test script on sample file
5. Commit and push clean baseline

## Quick Start
```bash
cd /Users/jaymangal/Desktop/personal_finance_tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set OPENAI_API_KEY in .env
```
