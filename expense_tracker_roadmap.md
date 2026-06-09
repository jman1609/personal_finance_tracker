# Expense Tracker Roadmap: End-to-End Plan and Task Backlog

## Purpose

Build a simple local personal finance tool for expense tracking, using files as the source of truth. For now, avoid SQLite or any external database. CSV/XLSX files stored in structured folders will act as the lightweight database.

The product should eventually let me:

1. Upload bank account statements, credit card statements, and other expense files.
2. Tell the app what each file represents, such as bank name, account/card, and statement type.
3. See what cumulative data the app already has.
4. See whether any months/accounts/cards appear to be missing.
5. Confirm that all required data has been provided.
6. Run ingestion, de-duplication, validation, and categorization.
7. Review high-confidence, low-confidence, and uncategorized transactions.
8. See simple charts and tables for expense breakdown.
9. Trigger deeper insights about spending patterns and areas for improvement.

---

# Part 1: End-to-End Plan

## 1. Overall Direction

The system should become a local, file-based expense intelligence tool.

The high-level flow should be:

```text
Upload files
→ classify source
→ parse transactions
→ validate parsed data
→ update file-based ledger
→ de-duplicate transactions
→ categorize transactions
→ validate calculations
→ review uncertain rows
→ generate dashboards
→ generate deeper insights
```

The most important principle is that the data foundation should be reliable before building sophisticated UI or insights. If parsing, de-duplication, or validation is wrong, the charts and AI insights will be misleading.

So the project should be built in this order:

```text
Reliable file-based DB
→ reliable ingestion
→ reliable de-duplication
→ reliable validation
→ categorization review loop
→ simple UI
→ dashboards
→ deeper insights
```

---

## 2. File-Based Database Approach

For now, the project should not use SQLite or any other database.

Instead, maintain a structured local folder that behaves like a lightweight database:

```text
expenses/db/
```

This folder should contain CSV/XLSX files that represent the app's persistent state.

Proposed files:

```text
expenses/db/uploaded_files.csv
expenses/db/master_ledger.csv
expenses/db/enriched_ledger.csv
expenses/db/ingestion_runs.csv
expenses/db/validation_results.csv
expenses/db/manual_reviews.csv
expenses/db/category_overrides.csv
```

The current category mapping can remain here for now:

```text
expenses/config/category_mapping.json
```

Later, if UI-based editing becomes easier with tables, category rules can move to:

```text
expenses/db/category_rules.csv
```

### Why this works for now

This is a good fit because:

- The tool is for personal use.
- The data volume is likely manageable.
- CSV/XLSX files are easy to inspect manually.
- There is no DB setup or migration complexity.
- Files can be backed up easily.
- The UI can still read/write these files reliably if we are careful.

### Important constraint

The file-based DB must still be treated like a real database:

- Use stable IDs.
- Avoid overwriting without backups.
- Write files safely using temp files where possible.
- Keep raw source files separate from normalized DB files.
- Preserve enough metadata to trace results back to source files.

---

## 3. Proposed Folder Structure

The project should continue moving toward this structure:

```text
personal_finance_tracker/
├── README.md
├── context.md
├── expense_tracker_roadmap.md
├── requirements.txt
├── expenses/
│   ├── src/
│   │   └── categorize_expenses.py
│   ├── config/
│   │   └── category_mapping.json
│   ├── data/
│   │   ├── raw/
│   │   ├── processed/
│   │   └── samples/
│   └── db/
│       ├── uploaded_files.csv
│       ├── master_ledger.csv
│       ├── enriched_ledger.csv
│       ├── ingestion_runs.csv
│       ├── validation_results.csv
│       ├── manual_reviews.csv
│       └── category_overrides.csv
└── investments/
    ├── raw/
    │   └── portfolio/
    └── ...
```

### Folder responsibilities

#### `expenses/data/raw/`

Stores private uploaded/input expense files:

- bank account statements
- credit card statements
- wallet/payment statements, if added later

These files should remain gitignored.

#### `expenses/data/processed/`

Stores generated reports and exports:

- categorized transaction workbook
- category summary workbook
- QA summary workbook
- charts exports, if needed later

These files should remain gitignored.

#### `expenses/db/`

Stores the file-based application state:

- canonical master ledger (core transaction data)
- enriched ledger (derived/categorized transaction data)
- uploaded file registry
- ingestion run history
- validation results
- manual review decisions
- transaction-level overrides

This folder may contain sensitive financial data, so it should also be gitignored except for `.gitkeep` or schema/sample files if needed.

#### `expenses/config/`

Stores non-sensitive configuration, especially categorization rules.

#### `investments/`

Stores investment-related files separately from expenses.

---

## 4. Canonical Transaction Model

Before adding UI, the project needs one clear transaction schema.

Every parser should output the same canonical columns, regardless of source type.

### `expenses/db/master_ledger.csv` (Core Transaction Data)

This table stores the absolute minimum, de-duplicated transaction data directly from parsing. It is the "immutable" record of each unique transaction.

Proposed canonical fields:

```text
TransactionId             # Primary key for transaction, UUID generated by our system
SourceFileId              # Foreign key to expenses/db/uploaded_files.csv
Date                      # Transaction date
PostedDate                # Optional, if different from Date (e.g., settlement date)
DescriptionRaw            # Original description from statement
DescriptionNormalized     # Cleaned-up description for matching/display
ReferenceNumber           # Cheque/Ref.No. from statement
WithdrawalAmount          # Original withdrawal amount
DepositAmount             # Original deposit amount
SignedAmount              # Derived: DepositAmount - WithdrawalAmount (for consistent calculations)
TransactionFingerprint    # Hash-like key for de-duplication, source-aware
CreatedAt                 # When this transaction row was first added to the ledger
UpdatedAt                 # When this transaction row was last updated (e.g., category change)
```

### `expenses/db/enriched_ledger.csv` (Derived/Categorized Transaction Data)

This table is the result of joining `master_ledger.csv` with `uploaded_files.csv` and then adding all *derived*, *categorization*, and *review* fields. This table is regenerated on each run based on the `master_ledger` and current rules.

Proposed canonical fields (includes all from `master_ledger.csv` plus):

```text
# From uploaded_files.csv join:
SourceType                # bank_account / credit_card / wallet / other
Institution               # HDFC / ICICI / SBI / Axis / etc.
AccountOrCardLast4        # Last 4 digits of account/card (from uploaded_files, not asked from user directly)
StatementPeriodStart      # From uploaded_files
StatementPeriodEnd        # From uploaded_files
SourceFileName            # Original filename from uploaded_files

# Derived transaction attributes:
Flow                      # INFLOW / OUTFLOW / NEUTRAL
PaymentMode               # Derived: UPI, Card, NEFT, RTGS, etc.
CounterpartyGuess         # Derived from narration
UPIHandle                 # Derived from narration
TxnIdGuess                # Derived from narration

# Categorization and Review:
Category
Subcategory
Merchant
CategorizationConfidence  # HIGH / MEDIUM / LOW / NONE
MatchedPattern            # Rule pattern that matched
NeedsReview               # Flag for human review
ReviewReason              # Explanation for review need
IsReversal                # True if part of a reversal pair
ReversalGroupId           # UUID for linked reversal transactions
```

### Why this matters

Without a canonical model, every new file type creates special cases. The UI and dashboards become messy.

With a canonical model:

- All parsers feed into the same core ledger.
- De-duplication becomes more reliable.
- Validation becomes easier.
- Dashboards become simpler.
- Review decisions persist consistently.
- Separation of concerns between core facts (`master_ledger`) and derived insights (`enriched_ledger`).

---

## 5. File Upload and Source Classification (UI-driven)

Eventually, the UI should allow uploads under clear categories.

Examples:

```text
Bank account statement → HDFC / ICICI / SBI / etc.
Credit card statement → HDFC card / Amazon ICICI / Axis / etc.
Wallet/payment app statement → Paytm / PhonePe / GPay, if needed later
```

For each uploaded file, the app should prompt the user for minimal, non-sensitive metadata and capture it:

```text
SourceType      # UI selects from dropdown: Bank Account / Credit Card / Wallet
Institution     # UI enters text or selects from known list: HDFC / ICICI / etc.
```

Crucially, we **will not ask for `AccountNumber` or `AccountLast4` directly from the user.** These can be inferred by the parser from the statement content as a "hint" and then saved into `uploaded_files.csv`, but not as a required direct input.

Captured metadata for `expenses/db/uploaded_files.csv`:

```text
SourceFileId              # Primary key for file, UUID generated by our system
OriginalFileName          # Name of file uploaded by user
StoredFilePath            # Internal path where we copy the file (e.g., expenses/data/raw/YYYY-MM/)
FileHash                  # SHA256 hash of the file content for exact duplicate detection
SourceType                # Bank Account / Credit Card / Wallet (from UI)
Institution               # HDFC / ICICI / etc. (from UI)
AccountOrCardLast4        # *Inferred by parser*, if found reliably in statement. Not asked from user.
StatementPeriodStart      # *Inferred by parser*, first transaction date
StatementPeriodEnd        # *Inferred by parser*, last transaction date
TotalTransactionsInFile   # *Inferred by parser*, count of parsed transactions
UploadedAt                # Timestamp when file was uploaded
ParsedStatus              # PENDING / PARSED / FAILED
ValidationStatus          # PENDING / PASS / WARN / FAIL
```

This `uploaded_files.csv` should store one row per *statement file*, with all its metadata.

The UI should not just accept a file and silently process it. It should first show:

```text
I found 186 transactions.
This appears to be HDFC Bank Account (XXXX0683 inferred).
Date range: 2026-05-01 to 2026-05-31.
Do you want to ingest this file?
```

---

## 6. Cumulative Data Coverage

Before running categorization, the app should tell me what data it already has.

Example:

```text
HDFC Bank XXXX0683
Available: Aug 2025 to Mar 2026
Missing/unknown: Apr 2026, May 2026

HDFC Credit Card XXXX5161
Available: Jan 2026 to Feb 2026
Missing/unknown: Mar 2026 onward
```

This helps avoid bad analysis caused by incomplete data.

The app should ask:

```text
Do you want to upload more files, or continue with the current dataset?
```

Coverage should be calculated from the `uploaded_files.csv` registry and `master_ledger.csv`.

---

## 7. Ingestion and De-duplication

Ingestion means converting source files into canonical transactions and adding only new transactions to the ledger.

The app should track each ingestion run in:

```text
expenses/db/ingestion_runs.csv
```

Each run should report:

```text
RunId
SourceFileId
RowsParsed
RowsAdded
RowsSkippedAsDuplicates
RowsFailedValidation
StartedAt
CompletedAt
Status
```

### De-duplication approach (Master Ledger Responsibility)

De-duplication logic will be the **sole responsibility of the process updating `master_ledger.csv`**.

The current script already has a basic transaction identity key. This needs to become a formal `TransactionFingerprint`.

`TransactionFingerprint` will be a hash-like key that uniquely identifies a transaction, robust to minor variations in downloads.

For bank accounts, fingerprint may use:

```text
Institution + AccountOrCardLast4 + Date + DescriptionNormalized + ReferenceNumber + SignedAmount + ClosingBalance
```

For credit cards, fingerprint may use:

```text
Institution + AccountOrCardLast4 + Date + DescriptionNormalized + ReferenceNumber + SignedAmount
```

The important point is that de-duplication should be **source-aware**. Bank account statements and credit card statements may not have the same columns or reliable `ClosingBalance`.

### Expected behavior

If the same file (same `FileHash`) is uploaded twice:

```text
Rows added: 0
Rows skipped as duplicates: all rows
```

If an overlapping statement is uploaded (same `SourceType`, `Institution`, `AccountOrCardLast4` for overlapping `StatementPeriodStart`/`End`):

```text
Only genuinely new transactions (based on `TransactionFingerprint`) should be added to `master_ledger.csv`.
```

Refunds and reversals should not be dropped as duplicates just because they share amount/date/merchant. They should remain in the `master_ledger` and be tagged separately (which will be reflected in `enriched_ledger.csv`).

---

## 8. Validation Loop

Validation should be built into the system, not treated as optional.

Every ingestion/categorization run should produce validation results.

Store them in:

```text
expenses/db/validation_results.csv
```

Validation checks should have this shape:

```text
RunId
CheckName
CheckLevel          # file / ledger / summary
Status              # PASS / WARN / FAIL
Details
CreatedAt
```

Where `Status` can be:

```text
PASS
WARN
FAIL
```

### File-level validation

Checks include:

- Was a header row found?
- Were required columns found?
- Were transaction rows parsed?
- Was a date range detected?
- Are dates valid?
- Are amounts valid?
- Was account/card identifier detected?

### Ledger-level validation

Checks include:

- No duplicate `TransactionFingerprint` values in `master_ledger.csv`.
- No missing transaction dates.
- No missing `SignedAmount`.
- No invalid inflow/outflow combinations.
- New row counts reconcile with ingestion run stats.

### Summary-level validation

Checks include:

- Category totals reconcile to total outflow spend (from `enriched_ledger.csv`).
- Categorized plus uncategorized rows equal total eligible rows.
- Inflows are excluded from expense summaries.
- Reversal-adjusted totals are separate from raw totals.

The UI should show these checks before showing final insights.

---

## 9. Categorization and Review Workflow

Categorization should remain mapping-first for now.

Current rule source:

```text
expenses/config/category_mapping.json
```

The categorizer will operate on `master_ledger.csv` (joined with `uploaded_files.csv`) to produce the derived categorization fields that go into `enriched_ledger.csv`.

The categorizer should classify transactions into confidence buckets:

```text
High confidence
Low/medium confidence
Uncategorized
Needs review
Possible reversal/refund
```

### High confidence

A single strong rule matched clearly.

### Low/medium confidence

A weaker heuristic matched, or multiple possible rules matched.

### Uncategorized

No rule matched.

### Needs review

Transactions that require manual attention:

- uncategorized
- multiple matches
- possible reversals
- invalid/missing data
- unusual transactions

Manual review decisions should persist in files such as:

```text
expenses/db/manual_reviews.csv
expenses/db/category_overrides.csv
```

The review flow should eventually allow:

```text
Assign category
Assign subcategory
Assign merchant
Create new rule from this choice
Apply this rule to similar transactions
```

---

## 10. Dashboard and Tables

After ingestion, de-duplication, validation, and categorization are reliable, the UI should show expense breakdowns.

Useful charts/tables:

```text
Monthly spend trend
Category-wise spend
Subcategory-wise spend
Merchant-wise spend
Top transactions
Recurring payments
Subscriptions
Refund/reversal-adjusted spend
Uncategorized spend percentage
Low-confidence spend percentage
```

Every chart should be based on the validated `enriched_ledger.csv` and should clearly state whether reversals are included or excluded.

---

## 11. Deeper Insights

Deeper insights should come after the dashboard layer.

Start with deterministic insights:

```text
Food delivery increased 28% month-over-month.
Subscriptions total ₹X/month across Y merchants.
Top 5 merchants account for Z% of spend.
Travel spend spiked in March due to Agoda booking.
Uncategorized spend is 12%, so insights may be incomplete.
```

Only later add AI-generated insights.

If AI is used, it should receive summarized data, not raw bank statements.

Good AI input:

```text
monthly category totals
top merchants
recurring payments
large unusual transactions
uncategorized percentage
validation warnings
```

Bad AI input:

```text
full raw bank statement rows with all sensitive details
```

---

## 12. UI Direction

Use Streamlit first.

Recommended stack:

```text
Python
Streamlit
Pandas
Plotly
OpenPyXL / xlrd
```

Reasons:

- Fast to build.
- Works locally.
- Easy file uploads.
- Good enough charts and tables.
- No need for separate frontend/backend yet.

The UI should initially be simple:

```text
Upload files
Preview parsed data
Show data coverage
Run categorizer
Show validation results
Show dashboard
Show review queue
```

---

## 13. Testing and Regression Protection

Because financial calculations must be trustworthy, the project needs tests.

Use anonymized sample files where possible.

At minimum, test:

```text
parser behavior
de-duplication behavior
validation checks
category summary reconciliation
reversal handling
manual override behavior
```

The goal is to prevent accidental changes from breaking previous behavior.

---

## 14. Documentation

- [ ] Update README with new file-based DB approach.
- [ ] Document how to add a new monthly statement.
- [ ] Document how to rerun categorization.
- [ ] Document how de-duplication works.
- [ ] Document how validation works.
- [ ] Document how to review uncategorized rows.
- [ ] Document how to add new category mapping rules.
- [ ] Document UI usage once UI exists.

## 15. Git / Privacy

- [ ] Ensure `expenses/data/raw/` is ignored.
- [ ] Ensure `expenses/data/processed/` is ignored.
- [ ] Ensure `expenses/db/*` is ignored, except for `.gitkeep`.
- [ ] Ensure `investments/raw/` is ignored.
- [ ] Ensure root-level private investment files are ignored.
- [ ] Commit only code, config, docs, schemas, and anonymized samples.
- [ ] Never commit real statements, generated ledgers, or private outputs.

---

# Part 2: Laundry List of Tasks

## Foundation / Structure

- [ ] Create `expenses/db/` folder (DONE).
- [ ] Add `expenses/db/.gitkeep` (DONE).
- [ ] Update `.gitignore` to ignore sensitive `expenses/db/*` while keeping `.gitkeep` (DONE).
- [ ] **Decision 1: Move `master_ledger.csv` from `expenses/data/processed/` to `expenses/db/` as the source of truth.** (Agreed: YES)
- [ ] Update script defaults (`DEFAULT_MASTER_LEDGER_PATH`) to use `expenses/db/master_ledger.csv`.
- [ ] Keep generated Excel reports in `expenses/data/processed/`.
- [ ] Document folder responsibilities in `README.md`.
- [ ] **Decision 2: Keep `expenses/config/category_mapping.json` as JSON for now.** (Agreed: YES)

## Canonical Schema Definition

- [ ] Officially define canonical transaction schema in documentation (`expense_tracker_roadmap.md` and/or a separate schema file).
- [ ] Define columns for `expenses/db/master_ledger.csv` (core transaction data).
- [ ] Define columns for `expenses/db/enriched_ledger.csv` (derived/categorized transaction data).
- [ ] Define columns for `expenses/db/uploaded_files.csv` (file metadata).
- [ ] Define columns for `expenses/db/ingestion_runs.csv` (ingestion history).
- [ ] Define columns for `expenses/db/validation_results.csv` (validation checks).
- [ ] Define columns for `expenses/db/manual_reviews.csv` (human review decisions).
- [ ] Define columns for `expenses/db/category_overrides.csv` (transaction-level category corrections).

## Parser Refactoring & Improvements

- [ ] Simplify `argparse` in `categorize_expenses.py`:
  - Remove `--input-dir`, `--sheet`, `--mapping`, `--master-ledger`, `--output`, `--summary` command-line arguments.
  - Script should internally use fixed paths (`expenses/data/raw/`, `expenses/config/category_mapping.json`, `expenses/db/master_ledger.csv`, `expenses/data/processed/`).
  - Accept *only* `--input <path_to_single_file>` for parsing a specific uploaded statement.
- [ ] Refactor `parse_statement` to separate statement metadata from transaction data:
  - Extract statement metadata (Institution, SourceType, AccountOrCardLast4, StatementPeriodStart/End, TotalTransactionsInFile) and return it separately.
  - The `parse_statement` function should receive explicit `SourceType`, `Institution` as parameters (from UI or `uploaded_files.csv`).
  - Update `extract_statement_metadata` to infer `AccountOrCardLast4`, `StatementPeriodStart/End`, `TotalTransactionsInFile` from the statement content, but not rely on them as primary identifiers from file.
  - Ensure parsed transactions output the new `master_ledger.csv` schema.
- [ ] **Decision 3: Uploaded files should be copied into controlled raw folders.** (Agreed: YES)
  - Implement copying uploaded files to `expenses/data/raw/YYYY-MM/original_filename.xls`.
  - Compute file hash (`FileHash`) for each uploaded file.
- [ ] **Task (Deferred): Develop a Flexible Parser Framework.** (Moved to later phase)
- [ ] Add basic pre-parsing hints for column identification (e.g., suggesting a "Date" column).
- [ ] Support handling single-sheet files, throwing an error for multiple sheets if necessary, or processing only the first relevant sheet.

## Ingestion Pipeline Enhancements

- [ ] Implement `uploaded_files.csv` management:
  - Add new entry for each unique uploaded file.
  - Detect if the same file (`FileHash`) was uploaded before.
  - Detect overlaps in `StatementPeriodStart`/`End` for same `SourceType`/`Institution`/`AccountOrCardLast4`.
- [ ] Implement `ingestion_runs.csv` management:
  - Record details of each ingestion run.
  - Report `RowsParsed`, `RowsAdded`, `RowsSkippedAsDuplicates`, `RowsFailedValidation`.

## De-duplication Enhancements (Master Ledger Responsibility)

- [ ] Update `compute_dedupe_key` to use the new `TransactionFingerprint` logic.
- [ ] Ensure `TransactionFingerprint` is robust and source-aware (e.g., handles missing `ClosingBalance` for credit cards).
- [ ] Ensure `master_ledger.csv` update process uses `TransactionFingerprint` for de-duplication (`keep="last"`).
- [ ] Add explicit tests for de-duplication scenarios (same file, overlapping files, refunds).
- [ ] Ensure refunds/reversals are preserved and tagged, not dropped as duplicates.

## Validation Loop Enhancements

- [ ] Implement `expenses/db/validation_results.csv` management.
- [ ] Add comprehensive file-level validation checks (e.g., header found, valid dates, amounts).
- [ ] Add comprehensive ledger-level validation checks (e.g., no duplicate `TransactionFingerprint`, no missing core data).
- [ ] Add comprehensive summary-level validation checks (e.g., category totals reconcile).
- [ ] Integrate validation results into CLI output (for now) and later UI.

## Categorization Workflow Refinement

- [ ] Update `categorize_with_mapping` to output to `enriched_ledger.csv` schema.
- [ ] Implement confidence labeling for categorization.
- [ ] Refine `detect_reversal_pairs` and ensure tagging is consistent in `enriched_ledger.csv`.
- [ ] Separate `create_summary` to operate on `enriched_ledger.csv`.

## Safe File Operations

- [ ] Ensure all `expenses/db/*.csv` and `expenses/db/*.xlsx` files use `safe_replace_with_backup` or similar safe write strategies.
- [ ] Implement backup strategy for `expenses/config/category_mapping.json`.

## UI (Streamlit)

- [ ] (Deferred) Begin Streamlit UI development (after backend foundation is solid).

## Testing

- [ ] Create anonymized sample statements (for HDFC Bank, Credit Card, etc.).
- [ ] Add unit tests for parser functions, metadata extraction, date/amount normalization.
- [ ] Add unit tests for de-duplication logic.
- [ ] Add unit tests for validation checks.
- [ ] Add integration tests for ingestion pipeline.

---

# Immediate Next Recommended Work (Revised)

The next best task, now that the `expenses/db/` folder is created, is to:

1.  **Refine Canonical Schema Definition (Phase 1).**
    *   Document the precise columns and their types/constraints for `master_ledger.csv` and `uploaded_files.csv` within `expense_tracker_roadmap.md`.
    *   Create empty CSV files for these in `expenses/db/` with headers as a schema definition.
2.  **Move `master_ledger.csv` location (Decision 1).**
    *   Move `expenses/data/processed/master_ledger.csv` to `expenses/db/master_ledger.csv`.
    *   Update `DEFAULT_MASTER_LEDGER_PATH` in `expenses/src/categorize_expenses.py`.

This sets the stage for refactoring the parser to use these new structures.
