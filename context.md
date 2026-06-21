# Project Context: Personal Finance Tracker

## Goal
Build a Git-ready Python project that reads bank/credit-card statements (CSV/XLS/XLSX), auto-categorizes transactions, and produces clean outputs for analysis. Eventually add a Streamlit UI with dashboards and deeper insights.

## Architecture Decisions

- **No SQLite.** Use CSV/XLSX files in `expenses/data/db/` as the file-based database.
- **Two-ledger model.** `master_ledger.csv` holds immutable core transaction data. `enriched_ledger.csv` is fully derived (categorization, review flags, reversal tags) and regenerated on each run.
- **De-duplication is the sole responsibility of the process writing `master_ledger.csv`.** `enriched_ledger.csv` never de-dupes.
- **`Institution` and `SourceType` come from UI input**, not inferred from statement content. Account/card last-4 is inferred by the parser as a hint only — never asked from the user directly.
- **No complex per-bank format config files.** Parser stays simple and general; a lightweight pre-parsing heuristic can suggest column mappings in the UI later.
- **Flexible Parser Framework deferred.** Nail the HDFC parser and core data model first; generalize later.
- **`SignedAmount` kept** (`DepositAmount - WithdrawalAmount`). **`Currency` omitted** for now — all transactions assumed INR; add if multi-currency is ever needed.
- **Safe writes everywhere.** All `expenses/data/db/` files use a `safe_replace_with_backup` pattern (`.backup` suffix) before any overwrite. Same applies to `expenses/config/category_mapping.json`.
- **`rebuild_enriched_ledger()` is the UI entry point for re-categorization.** It reads master_ledger + uploaded_files, re-runs enrichment and categorization, and rewrites enriched_ledger.csv. No ingestion required. This is what the UI calls when a user updates category rules.

## File-Based DB: `expenses/data/db/`

| File | Purpose |
|---|---|
| `uploaded_files.csv` | One row per statement file; metadata + parse/validation status |
| `master_ledger.csv` | Core de-duplicated transactions (immutable facts) |
| `enriched_ledger.csv` | Derived: categorization, review flags, reversal tags |
| `ingestion_runs.csv` | History of each ingestion run |
| `validation_results.csv` | Per-run validation check results |
| `manual_reviews.csv` | Human review decisions |
| `category_overrides.csv` | Transaction-level category corrections |

`expenses/config/category_mapping.json` stays as JSON for now (may move to `expenses/data/db/category_rules.csv` once UI editing is in place).

## Canonical Schema

### `master_ledger.csv`
```
TransactionId, SourceFileId, Date, PostedDate,
DescriptionRaw, DescriptionNormalized, ReferenceNumber,
WithdrawalAmount, DepositAmount, SignedAmount,
TransactionFingerprint, CreatedAt, UpdatedAt
```

### `enriched_ledger.csv`
All `master_ledger` columns (except `PostedDate`) plus:
```
SourceType, Institution, AccountOrCardLast4, SourceFileName,
Flow, PaymentMode, CounterpartyGuess, UPIHandle, TxnIdGuess, TxnNote,
Category, Subcategory, Merchant,
CategorizationConfidence, MatchedPattern,
NeedsReview, ReviewReason, IsReversal, ReversalGroupId
```

**Removed from enriched_ledger (2026-06-21):** `PostedDate` (always blank — HDFC has no separate posted date), `StatementPeriodStart`, `StatementPeriodEnd` (per-file metadata, not per-row — available in `uploaded_files.csv`).

**Added to enriched_ledger (2026-06-21):** `TxnNote` — UPI free-text note from the end of the narration (e.g. "JAN RENT", "PHOTOGRAPHER", "HALDI KA KURTA").

### `uploaded_files.csv`
```
SourceFileId, OriginalFileName, StoredFilePath, FileHash,
SourceType, Institution, AccountOrCardLast4,
StatementPeriodStart, StatementPeriodEnd, TotalTransactionsInFile,
UploadedAt, ParsedStatus, ValidationStatus
```

## `TransactionFingerprint` (De-dupe Key)
Source-aware hash. Bank accounts use:
`Institution + AccountOrCardLast4 + Date + DescriptionNormalized + ReferenceNumber + SignedAmount + ClosingBalance`

Credit cards omit `ClosingBalance`.

## Repo Conventions
- Expense code: `expenses/src/`
- Expense config: `expenses/config/`
- Raw input files: `expenses/data/raw/YYYY-MM/` (gitignored)
- Generated reports: `expenses/data/processed/` (gitignored)
- File-based DB: `expenses/data/db/` (gitignored except `.gitkeep`)
- Investment files: `investments/`

## Expected Inputs
- CSV / XLS / XLSX transaction files.
- Common columns: `Date`, `Narration`/`Description`, `Amount` or (`Withdrawal Amt.` + `Deposit Amt.`)

### HDFC `.xls` specifics
- Legacy `.xls` (CDFV2), requires `xlrd`
- Contains account header + transactions table + footer
- Headers: `Date`, `Narration`, `Chq./Ref.No.`, `Value Dt`, `Withdrawal Amt.`, `Deposit Amt.`, `Closing Balance`
- Parse by finding header row, dynamically detecting separator row (asterisks), keeping rows where first cell is a valid date

## Script Entry Points

**File:** `expenses/src/categorize_expenses.py`

### Ingest a new statement file:
```bash
python expenses/src/categorize_expenses.py \
  --input <path/to/statement.xlsx> \
  --institution HDFC \
  --source-type "Bank Account"
```

### Re-run categorization only (no new file):
```bash
python expenses/src/categorize_expenses.py --recategorize
```
Reads `master_ledger.csv` + `uploaded_files.csv`, re-runs enrichment and categorization using the current `category_mapping.json`, rewrites `enriched_ledger.csv`. Use this after updating category rules without ingesting a new file. This is the function the UI will call when the user clicks "Refresh Categorization".

**Output files created/updated (ingestion mode):**
- `expenses/data/db/master_ledger.csv` (core transactions, deduplicated)
- `expenses/data/db/uploaded_files.csv` (one row per ingested file)
- `expenses/data/db/ingestion_runs.csv` (one row per run)
- `expenses/data/db/enriched_ledger.csv` (denormalized, categorized view)
- `expenses/data/processed/categorized_transactions.xlsx` (Excel workbook)
- `expenses/data/processed/category_summary.xlsx` (summary by category/month)

**Output files updated (recategorize mode):**
- `expenses/data/db/enriched_ledger.csv` only

---

## PaymentMode Detection (as of 2026-06-21)

`detect_payment_mode(text)` in `categorize_expenses.py` uses keyword/regex rules to detect payment mode from narration. Previously used a naive dash-split which broke for ~50% of modes.

| Mode | Detection Rule |
|---|---|
| `UPI` | starts with `UPI-` or `REV-UPI-` |
| `ACH C` | starts with `ACH C-` |
| `ACH D` | starts with `ACH D-` |
| `NEFT CR` / `NEFT DR` | contains `NEFT CR` / `NEFT DR` |
| `RTGS CR` / `RTGS DR` | contains `RTGS CR` / `RTGS DR` |
| `IMPS` | starts with `IMPS` |
| `POS` | starts with `POS ` or `CRV POS ` |
| `NET BANKING SI` | contains `NET BANKING SI` |
| `DEBIT CARD SI` | starts with `ME DC SI ` |
| `DEBIT CARD INTL` | contains `.DC INTL POS TXN` |
| `AUTOPAY` | contains `AUTOPAY SI` |
| `FUND TRANSFER` | starts with `FT-` or contains `IB FUNDS TRANSFER` |
| `BILL PAYMENT` | starts with `IB BILLPAY` |
| `FIXED DEPOSIT` | starts with `FD THROUGH DIGITAL` or `IB FD` |
| `ATM WITHDRAWAL` | starts with `NWD` |
| `REVERSAL` | starts with `REV-` (non-UPI) |
| `FOREX TRANSFER` | starts with `RFX ` |
| `INTEREST` | contains `INTEREST PAID` or `QUARTERLY INTEREST` |
| `TAX` | starts with `TAX RECOVERY` or `CBDT/` |
| `BANK CHARGES` | starts with `LOCKER RENT` |
| `OTHER` | fallback |

## UPI Narration Structure

HDFC UPI format: `UPI-<NAME>-<handle@bank>-<IFSC>-<TxnId>-<FreeText>`

- `CounterpartyGuess` = segment 1 (person/merchant name)
- `UPIHandle` = regex match for `@`-pattern (e.g. `john@ybl`)
- `TxnIdGuess` = 10-20 digit number anywhere in narration
- `TxnNote` = last segment if not numeric / not a UPI handle / not IFSC / not a mode tag — this is the free-text note the sender typed (e.g. "JAN RENT", "PHOTOGRAPHER")

---

## Category Mapping: `expenses/config/category_mapping.json`

Rules are matched against `DescriptionNormalized` (case-insensitive substring match). First match wins.

**Current categories covered (35 rules as of 2026-06-21):**

| Pattern | Category | Subcategory |
|---|---|---|
| `ACH C` | Income | Dividend Income |
| `GROWW` | Investments | Mutual Funds / SIP |
| `ZERODHA` | Investments | Stocks |
| `CSHFREGRIPBROKINGPRI` | Investments | Bonds (Grip Broking) |
| `RAZPWINTWEALTH` | Investments | Bonds / Wealth Platform |
| `RAZPBSEINDIACOM` | Investments | Stocks / BSE |
| `FD THROUGH DIGITAL` | Investments | Fixed Deposit |
| `IB FD PREMAT` | Investments | Fixed Deposit |
| `QUARTERLY INTEREST CREDIT` | Income | FD Interest |
| `INTEREST PAID TILL` | Income | FD Interest |
| `AUTOPAY SI` | Financial | Credit Card Payment |
| `LOCKER RENT` | Financial | Bank Charges |
| `TAX RECOVERY` | Financial | TDS / Tax |
| `CBDT/` | Financial | Income Tax |
| `DC INTL POS TXN MARKUP` | Financial | Bank Charges |
| `RFX ` | Financial | Forex / Wire Transfer |
| `NET BANKING SI` | Internal Transfer | Own Account Transfer |
| `IB BILLPAY` | Bills | Bill Payment |
| `AIRTEL` (various patterns) | Bills | Broadband / Internet |
| `APOLLO PHARMAC` / `AKASH PHARMACY` | Health | Pharmacy |
| `SWIGGY` / `ZOMATO` | Food | Food Delivery |
| `UBER` / `OLA` | Transport | Taxi |
| `AMAZON` / `FLIPKART` | Shopping | Online Shopping |
| `BIGBASKET` / `BLINKIT` | Groceries | Online Grocery |
| `SPOTIFY` / `NETFLIX` / `PLAYSTATION` | Entertainment | Subscriptions |
| `GOOGLE PLAY` / `GOOGLEPLAY` / `AUDIBLE` | Entertainment | Subscriptions |
| `CLAUDE.AI` | Software | Subscriptions |
| `MICROSOFT` / `IND*MICROSOFT` | Software | Subscriptions |
| `GOOGLE CLOUD` | Software | Subscriptions |
| `AGODA` | Travel | Hotel |
| `PAYTM` | Transfers | Wallet |

**Remaining uncategorized (as of 2026-06-21):** ~1172/2285 rows (51%), mostly UPI outflows. Needs merchant-level rules added based on `CounterpartyGuess` patterns.

---

## Session Work

### 2026-06-10 — Fixing High-Priority Issues & Testing

1. **Fixed CSV Type Consistency** (commit b999ca7) — `.astype(str).fillna("")` in 3 places before concat
2. **Tested Scenarios 1–3** with real HDFC data (3 files, 2285 rows)
3. **Refactored Date Parsing** (commit 660be42) — string-only, junk values naturally fail
4. **Fixed enriched_ledger data loss bug** (commit fc882b3) — rebuild via master_ledger + uploaded_files join
5. **Built Streamlit dashboard** (commit 6dd7fa0) — `expenses/ui/dashboard.py`

### 2026-06-14 — Bug Fix Session + Testing Completion

1. **Fixed 23 critical/high/medium bugs** (commit 4fc0f0e) — data alignment, dedup, encoding, validation, etc.
2. **Added 26-test unit test suite** (commit 7a17c3c) — all passing
3. **Fixed critical date corruption** (commit d3db941) — `dayfirst=True` double-parsing caused 60% blank dates
4. **Added date format auto-detection** (commit 5d88bdc) — `detect_date_format()` examines all distinct dates, validates exactly one format, errors on ambiguity
5. **Backup retention policy** — keep only 1 backup per file (delete old before creating new)
6. **Completed all 7 test scenarios** — Scenarios 4, 6, 7 passed; re-ingested fresh (0 blank dates, 2285 rows)

### 2026-06-21 — PaymentMode, Categorization, Schema, TxnNote, Recategorize

1. **Fixed PaymentMode extraction** — replaced naive dash-split with `detect_payment_mode()` using keyword/regex rules. Now correctly identifies 23 distinct modes (UPI, ACH C/D, NEFT CR/DR, RTGS CR/DR, DEBIT CARD SI, AUTOPAY, POS, FUND TRANSFER, etc.)

2. **Expanded category_mapping.json** — 17 → 35 rules. Added: Zerodha, Grip Broking, Wint Wealth, BSE India, FD Open/Maturity, FD Interest, Credit Card Autopay, Airtel broadband, Apollo/Akash Pharmacy, BigBasket, Audible, Claude.ai, PlayStation, Google Cloud, Bank Charges, Forex transfers, Internal transfers, ACH C → Dividend Income.

3. **Schema cleanup in enriched_ledger** — removed `PostedDate` (always blank), `StatementPeriodStart`, `StatementPeriodEnd` (per-file metadata, not per-row).

4. **Added `TxnNote` field** — captures UPI free-text note from end of narration. Filters out numeric IDs, UPI handles, IFSC codes, and mode tags. Gives human-readable context like "JAN RENT", "PHOTOGRAPHER", "HALDI KA KURTA".

5. **Added `--recategorize` mode** — `rebuild_enriched_ledger()` reads master_ledger + uploaded_files, re-runs enrichment + categorization, rewrites enriched_ledger. No XLS parsing. Designed as the function the UI will call when user updates category rules.

6. **Re-ingested all 3 files** — 2285 rows, 0 blank dates, sum ₹661,855.55, all verified.

---

## Current Task Status

### ✅ Completed
- Schema design, refactor chunks 1–3
- 23 bugs fixed (commit 4fc0f0e)
- 26-test unit suite, all passing
- All 7 test scenarios passed
- Critical date corruption fix (dayfirst double-parse)
- Date format auto-detection
- Backup retention (keep latest only)
- Streamlit dashboard (`expenses/ui/dashboard.py`)
- PaymentMode detection overhaul (23 modes, keyword/regex)
- Category mapping expanded (35 rules)
- Schema cleanup (PostedDate / StatementPeriod removed from enriched)
- TxnNote field (UPI free-text note)
- `--recategorize` / `rebuild_enriched_ledger()` for UI use

### ⏳ Next (In Priority Order)
1. **Improve categorization coverage** — 1172/2285 (51%) still Uncategorized. Mostly UPI outflows. Group by `CounterpartyGuess` to find repeated merchants, add rules to `category_mapping.json`.
2. **UI: Category Rule Manager** — see UI Roadmap below.
3. **Credit card statement integration** — Jay to provide real HDFC CC statement. `detect_date_format()` should handle different date formats automatically; column layout may need parser adjustments.
4. **Merge bank + CC data** — single dashboard view (depends on #3).
5. **Medium priority** — transaction validation pre-write; persist reversal fields if needed for UI.

---

## UI Roadmap

**Current state:** Basic Streamlit dashboard at `expenses/ui/dashboard.py` — 4 tabs (category breakdown, monthly trends, top merchants, transaction table), date + category filters, KPI cards.

**Backend hooks already in place:**
- `rebuild_enriched_ledger()` — call this when user updates rules; no re-ingestion needed
- `category_overrides.csv` schema exists (not yet populated by code)
- All derived fields (PaymentMode, CounterpartyGuess, TxnNote, IsReversal, NeedsReview, ReviewReason) are in enriched_ledger

### Priority 1: Category Rule Manager
**Goal:** Let user update categorization rules without editing JSON manually.

- Table view of all rules in `category_mapping.json` (pattern, category, subcategory, merchant)
- Add new rule form
- Edit / delete existing rule
- "Refresh Categorization" button → calls `rebuild_enriched_ledger()`, shows updated coverage %
- **Why first:** Directly unblocks the 51% uncategorized problem; highest leverage feature

### Priority 2: Dashboard Improvements
**Goal:** Make spend numbers accurate and useful.

Missing from current dashboard:
- **Account filter** — filter by `AccountOrCardLast4` (currently all accounts merged)
- **Flow filter** — separate INFLOW vs OUTFLOW views
- **Exclude Internal Transfers toggle** — CC payments and own-account transfers inflate spend; should be off by default
- **Reversal-aware totals** — exclude `IsReversal=1` rows from spend calculations (data already flagged, not used in dashboard)
- **TxnNote column** in transaction details table
- **PaymentMode breakdown** — chart showing spend split by UPI / ACH / NEFT / POS etc.

### Priority 3: File Ingestion
**Goal:** Move away from CLI; user uploads files from the UI.

- File upload widget (.xls/.xlsx)
- Institution dropdown (HDFC, ICICI, etc.)
- Source Type dropdown (Bank Account, Credit Card)
- Triggers ingestion pipeline, shows result: rows parsed / added / skipped
- Upload history table from `uploaded_files.csv`

### Priority 4: Review Queue
**Goal:** Surface transactions that need manual attention.

- Table of all rows where `NeedsReview=True`, grouped by `ReviewReason`:
  - `NO_MATCH` — no category rule matched
  - `MULTIPLE_MATCHES` — ambiguous categorization
  - `REVERSAL_SUSPECTED` — potential refund pair
  - `NO_DATE` — date could not be parsed
- Allow user to set category manually for a row → writes to `category_overrides.csv`
- **Note:** `category_overrides.csv` schema exists; backend logic to apply overrides during enrichment not yet implemented

### Priority 5: Ingestion History (nice to have)
- Table view of `ingestion_runs.csv` — run date, file, rows parsed/added/skipped
- Table view of `uploaded_files.csv` — all files ever ingested with status

---

## Current Data State (as of 2026-06-21)
- `master_ledger.csv`: 2285 rows, 0 blank dates
- `enriched_ledger.csv`: 2285 rows, 14 IsReversal=1 (7 pairs), 1172 Uncategorized (51%)
- Sum(SignedAmount): ₹661,855.55 (reconciled master ↔ enriched)
- 3 source files: Acct 0112 (250 rows), Acct 0683 two statements (1240 + 795 new after dedup)
- `expenses/data/db/`: exactly 1 backup file per CSV

---

## Security / Git Hygiene
Never commit: real bank/credit-card statements, `.env`, generated outputs, virtual environments, `expenses/data/db/` data files.

---

## Known Issues & Tech Debt

### Medium Priority (Known Limitations, Defer)

**1. Transaction Fingerprint Collision** — Theoretically, two transactions on same day with identical amount, description, and closing balance could collide. Risk extremely low for personal finances. Future: add sequence number to fingerprint if needed.

**2. Account Extraction Logic Duplicated** — Parsed in both `extract_statement_metadata()` and within `parse_statement()`. Centralize into helper.

**3. Reversal Fields Not Persisted** — `ReversalPairWithRefNo`, `Tag` computed but not in `ENRICHED_LEDGER_COLUMNS`. Add if needed for UI.

**4. HDFC Parser Format-Fragile** — Separator detection uses asterisks; may break on format changes. Clear error if not found.

**5. Missing Transaction Validation** — No schema checks before master_ledger write. Current filtering by `looks_like_date()` is baseline.

### Low Priority (MVP Trade-offs)
- StartedAt/CompletedAt use same timestamp (ingestion duration not measured)
- Full-file hashing reads entire file into memory (fine for <10MB)
- Timezone inconsistency (UTC timestamps, naive transaction dates — assume INR)
- CSV storage won't scale indefinitely (SQLite/DuckDB upgrade possible later)

---

## Testing Plan

### Test Scenarios

#### Scenario 1: Basic Single-File Ingestion (Happy Path) ✅ PASSED (2026-06-10)
- Account 0112: 250 rows, 2025-12-11 to 2026-06-05
- All CSVs created, counts match, date parsing correct

#### Scenario 2: De-duplication (Rerun Same File) ✅ PASSED (2026-06-10)
- Re-run of Account 0112: 0 rows added (all 250 skipped as duplicates)

#### Scenario 3: Partial Overlap (Overlapping Statements) ✅ PASSED (2026-06-10)
- Account 0683 first statement: 1240 rows
- Account 0683 second statement: 1567 parsed, 772 duplicates, 795 new rows added
- Total master_ledger: 2285 rows

#### Scenario 4: Reversal Pairing ✅ PASSED (2026-06-14)
- 7 reversal pairs (14 rows) detected, all legitimate:
  - 2x Shubhi Gupta UPI pairs (₹8700, same day)
  - 1x Himanshu Chourasiya UPI refund (₹250)
  - 1x Zomato order + Razorpay refund (₹354.20)
  - 3x monthly inter-account transfers (0683↔0112, ₹50000 each)
- No false positives

#### Scenario 5: Categorization Coverage ✅ PASSED (ongoing)
- Coverage improving iteratively via category_mapping.json
- 49% categorized as of 2026-06-21 (1113/2285 rows)

#### Scenario 6: Data Type Consistency ✅ PASSED (2026-06-14)
- Sum(SignedAmount) reconciles exactly: ₹661,855.55
- No "nan" strings; 0 blank dates

#### Scenario 7: Error Handling ✅ PASSED (2026-06-14)
- Missing --institution → clean argparse error
- Invalid file path → FileNotFoundError
- Missing category_mapping.json → clean error with path
- Malformed Excel → "Could not find transaction header row"
- Empty statement → clean ValueError

### Regression Tests
After any future refactors:
1. Re-run all scenarios
2. Spot-check numeric reconciliation (sum SignedAmount)
3. Verify no new "nan" strings in CSVs
4. Check 0 blank dates
5. Run unit tests: `python -m pytest expenses/tests/test_categorize_expenses.py -v`

---

## 🔴 Critical Bug Fixed (2026-06-14): Date Corruption via Repeated `dayfirst=True` Parsing

Root cause: `parse_date_series()` applied `dayfirst=True` unconditionally. The pipeline re-parses dates multiple times as they round-trip through `.astype(str)` → CSV → re-read. ISO dates (`2026-01-23`) re-parsed with `dayfirst=True` → dateutil swaps last two components → month=23 → NaT (blank) for any day > 12. ~60% blank dates.

Fix: `parse_date_series()` tries `format="%Y-%m-%d"` first (unambiguous), only falls back to `dayfirst=True` for non-ISO strings. `detect_date_format()` handles first parse of raw statement dates by examining all distinct values.

**Lesson:** Never apply `dayfirst=True` to values that may already be ISO format. Any column that round-trips through CSV needs format-aware parsing.

---

## Quick Start
```bash
cd /Users/jaymangal/Desktop/personal_finance_tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Ingest a file:
```bash
python expenses/src/categorize_expenses.py --input <file.xls> --institution HDFC --source-type "Bank Account"
```

### Re-run categorization after updating rules:
```bash
python expenses/src/categorize_expenses.py --recategorize
```

### Run unit tests:
```bash
python -m pytest expenses/tests/test_categorize_expenses.py -v
```

### Launch dashboard:
```bash
streamlit run expenses/ui/dashboard.py
```
