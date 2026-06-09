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

- canonical master ledger
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

Proposed canonical fields:

```text
TransactionId
SourceFileId
SourceFileName
SourceType
Institution
AccountOrCardLast4
StatementPeriodStart
StatementPeriodEnd
Date
PostedDate
DescriptionRaw
DescriptionNormalized
ReferenceNumber
WithdrawalAmount
DepositAmount
SignedAmount
Currency
ClosingBalance
Flow
PaymentMode
CounterpartyGuess
UPIHandle
TxnIdGuess
TransactionFingerprint
Category
Subcategory
Merchant
CategorizationConfidence
MatchedPattern
NeedsReview
ReviewReason
IsReversal
ReversalGroupId
CreatedAt
UpdatedAt
```

### Why this matters

Without a canonical model, every new file type creates special cases. The UI and dashboards become messy.

With a canonical model:

- all parsers feed into the same ledger
- de-duplication becomes more reliable
- validation becomes easier
- dashboards become simpler
- review decisions persist consistently

---

## 5. File Upload and Source Classification

Eventually, the UI should allow uploads under clear categories.

Examples:

```text
Bank account statement → HDFC / ICICI / SBI / etc.
Credit card statement → HDFC card / Amazon ICICI / Axis / etc.
Wallet/payment app statement → Paytm / PhonePe / GPay, if needed later
```

For each uploaded file, the app should capture metadata:

```text
SourceFileId
OriginalFileName
StoredFilePath
FileHash
SourceType
Institution
AccountOrCardLast4
StatementPeriodStart
StatementPeriodEnd
UploadedAt
ParsedStatus
ValidationStatus
```

This should be stored in:

```text
expenses/db/uploaded_files.csv
```

The UI should not just accept a file and silently process it. It should first show:

```text
I found 186 transactions.
This appears to be HDFC Bank Account XXXX0683.
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

Coverage should be calculated from the file registry and the master ledger.

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

### De-duplication approach

The current script already has a basic transaction identity key. This needs to become a formal `TransactionFingerprint`.

For bank accounts, fingerprint may use:

```text
Institution
AccountOrCardLast4
Date
DescriptionNormalized
ReferenceNumber
SignedAmount
ClosingBalance
```

For credit cards, fingerprint may use:

```text
Institution
AccountOrCardLast4
Date
DescriptionNormalized
ReferenceNumber
SignedAmount
```

The important point is that de-duplication should be source-aware. Bank account statements and credit card statements may not have the same columns.

### Expected behavior

If the same file is uploaded twice:

```text
Rows added: 0
Rows skipped as duplicates: all rows
```

If an overlapping statement is uploaded:

```text
Only genuinely new transactions should be added.
```

Refunds and reversals should not be dropped as duplicates just because they share amount/date/merchant. They should remain in the ledger and be tagged separately.

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
CheckLevel
Status
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

- No duplicate transaction fingerprints.
- No missing transaction dates.
- No missing signed amounts.
- No invalid inflow/outflow combinations.
- New row counts reconcile with ingestion run stats.

### Summary-level validation

Checks include:

- Category totals reconcile to total outflow spend.
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

Every chart should be based on the validated master ledger and should clearly state whether reversals are included or excluded.

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

# Part 2: Laundry List of Tasks

## Foundation / Structure

- [ ] Create `expenses/db/` folder.
- [ ] Add `expenses/db/.gitkeep`.
- [ ] Update `.gitignore` to ignore sensitive `expenses/db/*.csv` and `expenses/db/*.xlsx` while keeping `.gitkeep`.
- [ ] Decide whether `master_ledger.csv` should move from `expenses/data/processed/` to `expenses/db/`.
- [ ] If yes, update script defaults to use `expenses/db/master_ledger.csv` as the source of truth.
- [ ] Keep generated Excel reports in `expenses/data/processed/`.
- [ ] Document folder responsibilities in `README.md`.

## Canonical Schema

- [ ] Define canonical transaction schema in documentation.
- [ ] Add a schema file if useful, e.g. `expenses/config/transaction_schema.json`.
- [ ] Map existing HDFC statement columns to canonical columns.
- [ ] Preserve raw source fields separately from normalized fields.
- [ ] Add `TransactionId` generation.
- [ ] Add `SourceFileId` support.
- [ ] Add `TransactionFingerprint` support.
- [ ] Add `CategorizationConfidence` column.
- [ ] Add `CreatedAt` and `UpdatedAt` columns.

## Uploaded File Registry

- [ ] Create `expenses/db/uploaded_files.csv`.
- [ ] Define columns for uploaded file registry.
- [ ] Compute file hash for every ingested file.
- [ ] Detect if the exact same file was previously uploaded.
- [ ] Store original filename.
- [ ] Store internal/stored path.
- [ ] Store source type.
- [ ] Store institution.
- [ ] Store account/card last4.
- [ ] Store detected statement period.
- [ ] Store uploaded/imported timestamp.
- [ ] Store parse status.
- [ ] Store validation status.

## Ingestion Runs

- [ ] Create `expenses/db/ingestion_runs.csv`.
- [ ] Define ingestion run columns.
- [ ] Generate `RunId` for every ingestion run.
- [ ] Track rows parsed.
- [ ] Track rows added.
- [ ] Track rows skipped as duplicates.
- [ ] Track rows failed validation.
- [ ] Track run start/end timestamps.
- [ ] Track run status.
- [ ] Print ingestion summary in CLI.
- [ ] Later, show ingestion summary in UI.

## Parser Improvements

- [ ] Keep current HDFC bank parser working.
- [ ] Refactor parser output to canonical schema.
- [ ] Make parser source-aware.
- [ ] Support single-file ingestion through `--input`.
- [ ] Support folder ingestion through `--input-dir`.
- [ ] Expand filename patterns or remove dependency on filename patterns.
- [ ] Add parser metadata extraction for statement period.
- [ ] Add parser metadata extraction for account/card last4.
- [ ] Add parser metadata extraction for institution.
- [ ] Add credit card parser support.
- [ ] Add CSV parser support later.

## De-duplication

- [ ] Define bank-account transaction fingerprint.
- [ ] Define credit-card transaction fingerprint.
- [ ] Normalize description before fingerprinting.
- [ ] Normalize dates before fingerprinting.
- [ ] Normalize amounts before fingerprinting.
- [ ] Include institution in fingerprint.
- [ ] Include account/card last4 in fingerprint.
- [ ] Include reference number when available.
- [ ] Include closing balance for bank statements when available.
- [ ] Avoid treating refunds/reversals as duplicates.
- [ ] Store duplicate count per ingestion run.
- [ ] Add duplicate report output.
- [ ] Test same file uploaded twice.
- [ ] Test overlapping statements.
- [ ] Test same date/amount but different merchant.

## Validation

- [ ] Create `expenses/db/validation_results.csv`.
- [ ] Define validation result columns.
- [ ] Add file-level validation checks.
- [ ] Add ledger-level validation checks.
- [ ] Add summary-level validation checks.
- [ ] Add validation status to CLI output.
- [ ] Add validation sheet to output workbook.
- [ ] Add PASS/WARN/FAIL statuses.
- [ ] Fail or warn when required columns are missing.
- [ ] Warn when many transactions are uncategorized.
- [ ] Warn when duplicate fingerprints exist.
- [ ] Warn when amount parsing creates zeros unexpectedly.
- [ ] Reconcile category totals to total eligible spend.
- [ ] Reconcile categorized and uncategorized rows to total rows.
- [ ] Ensure inflows are excluded from expense summaries.
- [ ] Ensure reversal-adjusted totals are clearly separated from raw totals.

## Categorization

- [ ] Keep `expenses/config/category_mapping.json` as current rule source.
- [ ] Add confidence labels.
- [ ] Mark single strong rule matches as high confidence.
- [ ] Mark multiple matches as needs review.
- [ ] Mark no matches as uncategorized.
- [ ] Create low-confidence bucket for heuristic matches later.
- [ ] Keep `MatchedPattern` for explainability.
- [ ] Preserve `AllMatchedPatterns` for review.
- [ ] Improve merchant normalization.
- [ ] Improve reversal/refund tagging.
- [ ] Ensure reversals are excluded from spend summaries by default.

## Manual Review Loop

- [ ] Create `expenses/db/manual_reviews.csv`.
- [ ] Create `expenses/db/category_overrides.csv`.
- [ ] Define manual review columns.
- [ ] Define category override columns.
- [ ] Allow transaction-specific category override.
- [ ] Apply overrides before or after rule matching based on chosen logic.
- [ ] Persist review decisions across reruns.
- [ ] Add review queue output.
- [ ] Later, add UI editor for review queue.
- [ ] Later, allow creating new mapping rules from reviewed rows.

## Outputs and Reports

- [ ] Keep `categorized_transactions.xlsx` generation.
- [ ] Keep `category_summary.xlsx` generation.
- [ ] Add validation results sheet.
- [ ] Add ingestion summary sheet.
- [ ] Add duplicate summary sheet.
- [ ] Add confidence summary sheet.
- [ ] Add monthly trend summary.
- [ ] Add merchant summary.
- [ ] Add recurring payments summary.
- [ ] Add subscription summary.
- [ ] Add reversal-adjusted summary.

## Data Coverage

- [ ] Calculate date coverage by source.
- [ ] Calculate month coverage by source.
- [ ] Show first transaction date and last transaction date per source.
- [ ] Detect missing months where possible.
- [ ] Detect overlapping file periods.
- [ ] Detect accounts/cards with no recent data.
- [ ] Produce coverage report as CSV/XLSX.
- [ ] Later, show coverage report in UI.

## UI

- [ ] Add Streamlit dependency.
- [ ] Create `expenses/ui/app.py` or similar.
- [ ] Add upload page.
- [ ] Add source classification form.
- [ ] Add parsed preview table.
- [ ] Add ingestion confirmation step.
- [ ] Add current data coverage page.
- [ ] Add run categorizer button.
- [ ] Add validation results page.
- [ ] Add expense dashboard page.
- [ ] Add category breakdown chart.
- [ ] Add monthly spend chart.
- [ ] Add merchant breakdown table.
- [ ] Add top transactions table.
- [ ] Add review queue page.
- [ ] Add manual category assignment UI.
- [ ] Save review decisions to file-based DB.
- [ ] Add export/download buttons.

## Insights

- [ ] Generate deterministic monthly insights.
- [ ] Generate category trend insights.
- [ ] Generate merchant concentration insights.
- [ ] Detect recurring payments.
- [ ] Detect subscriptions.
- [ ] Detect unusual large expenses.
- [ ] Detect month-over-month spikes.
- [ ] Add warning when insights are based on incomplete/uncategorized data.
- [ ] Create structured summary payload for optional AI insights.
- [ ] Add optional AI insights later.
- [ ] Store generated insight reports locally.

## Testing

- [ ] Create anonymized sample statements.
- [ ] Add tests for HDFC parser.
- [ ] Add tests for date parsing.
- [ ] Add tests for amount parsing.
- [ ] Add tests for transaction fingerprinting.
- [ ] Add tests for duplicate detection.
- [ ] Add tests for overlapping statements.
- [ ] Add tests for categorization rules.
- [ ] Add tests for multiple matches.
- [ ] Add tests for reversal pairing.
- [ ] Add tests for summary reconciliation.
- [ ] Add tests for validation checks.
- [ ] Add expected output fixtures.

## Documentation

- [ ] Update README with new file-based DB approach.
- [ ] Document how to add a new monthly statement.
- [ ] Document how to rerun categorization.
- [ ] Document how de-duplication works.
- [ ] Document how validation works.
- [ ] Document how to review uncategorized rows.
- [ ] Document how to add new category mapping rules.
- [ ] Document UI usage once UI exists.

## Git / Privacy

- [ ] Ensure `expenses/data/raw/` is ignored.
- [ ] Ensure `expenses/data/processed/` is ignored.
- [ ] Ensure `expenses/db/*.csv` and `expenses/db/*.xlsx` are ignored.
- [ ] Ensure `investments/raw/` is ignored.
- [ ] Ensure root-level private investment files are ignored.
- [ ] Commit only code, config, docs, schemas, and anonymized samples.
- [ ] Never commit real statements, generated ledgers, or private outputs.

---

# Immediate Next Recommended Work

The next best task is:

```text
Create expenses/db/ structure and define the file-based DB schemas.
```

That means:

1. Create `expenses/db/.gitkeep`.
2. Update `.gitignore` for `expenses/db/` sensitive files.
3. Decide and document schemas for:
   - `uploaded_files.csv`
   - `ingestion_runs.csv`
   - `validation_results.csv`
   - `manual_reviews.csv`
   - `category_overrides.csv`
4. Decide whether to move `master_ledger.csv` from `expenses/data/processed/` to `expenses/db/master_ledger.csv`.

Recommendation:

```text
Move master_ledger.csv to expenses/db/master_ledger.csv as the source of truth.
Keep expenses/data/processed/ only for generated reports and exports.
```

---

# Open Decisions

## Decision 1: Master ledger location

Recommended:

```text
expenses/db/master_ledger.csv
```

Reason:

The master ledger is not just a generated report. It is the source of truth for cumulative transactions.

## Decision 2: Category rules format

Recommended for now:

```text
expenses/config/category_mapping.json
```

Reason:

It already works and is flexible.

Possible future move:

```text
expenses/db/category_rules.csv
```

Reason:

CSV may be easier to edit through a table UI.

## Decision 3: Uploaded file handling

Recommended:

```text
Copy uploaded files into controlled raw folders instead of moving user-selected files unexpectedly.
```

Possible storage format:

```text
expenses/data/raw/YYYY-MM/source_file_name.xls
```

## Decision 4: UI framework

Recommended:

```text
Streamlit
```

Reason:

Fastest path to a local upload/review/dashboard tool.
