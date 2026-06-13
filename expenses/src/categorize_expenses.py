import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd


# --- Hardcoded internal paths ---
MAPPING_PATH = "expenses/config/category_mapping.json"
MASTER_LEDGER_PATH = "expenses/db/master_ledger.csv"
UPLOADED_FILES_PATH = "expenses/db/uploaded_files.csv"
INGESTION_RUNS_PATH = "expenses/db/ingestion_runs.csv"
ENRICHED_LEDGER_PATH = "expenses/db/enriched_ledger.csv"
OUTPUT_PATH = "expenses/data/processed/categorized_transactions.xlsx"
SUMMARY_PATH = "expenses/data/processed/category_summary.xlsx"

# --- HDFC-specific parser constants ---
HDFC_HEADER_KEYWORDS = [
    "date",
    "narration",
    "withdrawal amt.",
    "deposit amt.",
    "closing balance",
]

# --- Canonical schemas ---
MASTER_LEDGER_COLUMNS = [
    "TransactionId",
    "SourceFileId",
    "Date",
    "PostedDate",
    "DescriptionRaw",
    "DescriptionNormalized",
    "ReferenceNumber",
    "WithdrawalAmount",
    "DepositAmount",
    "SignedAmount",
    "TransactionFingerprint",
    "CreatedAt",
    "UpdatedAt",
]

UPLOADED_FILES_COLUMNS = [
    "SourceFileId",
    "OriginalFileName",
    "StoredFilePath",
    "FileHash",
    "SourceType",
    "Institution",
    "AccountOrCardLast4",
    "StatementPeriodStart",
    "StatementPeriodEnd",
    "TotalTransactionsInFile",
    "UploadedAt",
    "ParsedStatus",
    "ValidationStatus",
]

INGESTION_RUNS_COLUMNS = [
    "RunId",
    "SourceFileId",
    "RowsParsed",
    "RowsAdded",
    "RowsSkippedAsDuplicates",
    "RowsFailedValidation",
    "StartedAt",
    "CompletedAt",
    "Status",
]

ENRICHED_LEDGER_COLUMNS = [
    # Core transaction data (from master_ledger)
    "TransactionId",
    "SourceFileId",
    "Date",
    "PostedDate",
    "DescriptionRaw",
    "DescriptionNormalized",
    "ReferenceNumber",
    "WithdrawalAmount",
    "DepositAmount",
    "SignedAmount",
    "TransactionFingerprint",
    "CreatedAt",
    "UpdatedAt",
    # File metadata (from uploaded_files)
    "SourceType",
    "Institution",
    "AccountOrCardLast4",
    "StatementPeriodStart",
    "StatementPeriodEnd",
    "SourceFileName",
    # Derived transaction attributes
    "Flow",
    "PaymentMode",
    "CounterpartyGuess",
    "UPIHandle",
    "TxnIdGuess",
    # Categorization
    "Category",
    "Subcategory",
    "Merchant",
    "CategorizationConfidence",
    "MatchedPattern",
    "NeedsReview",
    "ReviewReason",
    # Reversal tracking
    "IsReversal",
    "ReversalGroupId",
]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def normalize_account_last4(value: str) -> str:
    s = "" if value is None else str(value).strip()
    digits = re.sub(r"\D+", "", s)
    return digits.zfill(4)[-4:] if digits else ""


def extract_account_last4_from_header(header_text: str) -> str:
    """Extract account last 4 digits from statement header text."""
    account_no_match = re.search(r"Account\s*No\s*:?\s*(\d{8,})", header_text, flags=re.IGNORECASE)
    account_no = account_no_match.group(1) if account_no_match else ""
    return normalize_account_last4(account_no[-4:] if len(account_no) >= 4 else "")


def normalize_description(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def compute_file_hash(file_path: str) -> str:
    """SHA-256 hash of file contents."""
    with open(file_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def compute_transaction_fingerprint(
    institution: str,
    account_last4: str,
    date_str: str,
    description_normalized: str,
    reference_number: str,
    signed_amount: float,
    closing_balance: str,
) -> str:
    """SHA-256 fingerprint for stable cross-run de-duplication.

    Closing balance is normalized to remove formatting variations (commas, spaces).
    """
    normalized_cb = closing_balance.replace(",", "").replace(" ", "").strip() if closing_balance else ""
    try:
        normalized_cb = f"{float(normalized_cb):.2f}"
    except (ValueError, TypeError):
        normalized_cb = ""

    signed_amount_str = f"{float(signed_amount):.2f}" if signed_amount else "0.00"

    parts = "|".join([
        institution.upper().strip(),
        account_last4,
        date_str,
        description_normalized.upper(),
        reference_number,
        signed_amount_str,
        normalized_cb,
    ])
    return hashlib.sha256(parts.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def looks_like_date(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, (pd.Timestamp, datetime)):
        return True
    s = str(value).strip()
    if not s or s == "nan":
        return False
    return pd.notna(pd.to_datetime(s, errors="coerce", dayfirst=True))


def parse_date_series(values: pd.Series) -> pd.Series:
    """Parse string date column into datetime64[ns]."""
    if values is None:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(values, errors="coerce", dayfirst=True)


# ---------------------------------------------------------------------------
# HDFC statement parsing
# ---------------------------------------------------------------------------

def find_header_row(df: pd.DataFrame) -> Tuple[int, int]:
    """Find header row and separator row. Returns (header_idx, first_data_idx).

    Robust to formatting variations: case-insensitive, handles extra whitespace.
    """
    header_idx = None
    for idx, row in df.iterrows():
        row_values = [str(v).strip().lower() for v in row.tolist() if pd.notna(v)]
        line = " ".join(row_values)
        if all(k in line for k in HDFC_HEADER_KEYWORDS):
            header_idx = idx
            break

    if header_idx is None:
        raise ValueError(
            "Could not find transaction header row in statement.\n"
            f"Expected to find these keywords: {', '.join(HDFC_HEADER_KEYWORDS)}\n"
            "Check that the statement format matches HDFC's expected structure."
        )

    separator_idx = None
    for idx in range(header_idx + 1, min(header_idx + 5, len(df))):
        row_values = [str(v).strip() for v in df.iloc[idx].tolist()]
        asterisk_count = sum(1 for v in row_values if v and "*" in v)
        if asterisk_count >= len(row_values) * 0.5:
            separator_idx = idx
            break

    if separator_idx is None:
        separator_idx = header_idx + 1

    first_data_idx = separator_idx + 1
    return header_idx, first_data_idx


def extract_statement_metadata(raw: pd.DataFrame, header_row: int, tx_df: pd.DataFrame, institution: str) -> Dict[str, str]:
    """Extract HDFC statement metadata from parsed transactions and header.

    Returns metadata for uploaded_files.csv row.
    """
    # Extract AccountLast4 from header
    header_block = raw.iloc[:header_row].astype(str)
    flat_text = "\n".join(
        " ".join([c for c in row.tolist() if c and c != "nan"])
        for _, row in header_block.iterrows()
    )
    last4 = extract_account_last4_from_header(flat_text)

    # Extract period from transaction dates
    if not tx_df.empty and "Date" in tx_df.columns:
        if pd.api.types.is_datetime64_any_dtype(tx_df["Date"]):
            dates = tx_df["Date"]
        else:
            dates = pd.to_datetime(tx_df["Date"], errors="coerce", dayfirst=True)
        valid_dates = dates[dates.notna()].sort_values()
        period_start = valid_dates.iloc[0].date().isoformat() if len(valid_dates) > 0 else ""
        period_end = valid_dates.iloc[-1].date().isoformat() if len(valid_dates) > 0 else ""
    else:
        period_start = period_end = ""

    return {
        "AccountOrCardLast4": last4,
        "StatementPeriodStart": period_start,
        "StatementPeriodEnd": period_end,
        "TotalTransactionsInFile": len(tx_df),
    }


def parse_statement(
    input_path: str,
    source_file_id: str,
    institution: str,
    source_type: str,
) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Parse an HDFC bank statement and return (transactions_df, statement_metadata).

    Transactions are in canonical master_ledger schema.
    Metadata is for uploaded_files.csv row.
    """
    now = datetime.now(timezone.utc).isoformat()

    raw = pd.read_excel(input_path, sheet_name=0, header=None)
    header_row, first_data_row = find_header_row(raw)

    tx = raw.iloc[first_data_row:].copy()

    if len(tx.columns) < 7:
        raise ValueError(f"Expected at least 7 columns, found {len(tx.columns)}. Statement format may be invalid.")

    tx = tx.iloc[:, :7]
    tx.columns = ["Date", "Narration", "RefNo", "ValueDate", "WithdrawalAmt", "DepositAmt", "ClosingBalance"]
    tx["Date"] = tx["Date"].astype(str)
    tx = tx[tx["Date"].apply(looks_like_date)].copy()

    tx["Date"] = parse_date_series(tx["Date"])
    tx["Narration"] = tx["Narration"].apply(lambda x: "" if pd.isna(x) else str(x).strip())
    tx["RefNo"] = tx["RefNo"].apply(lambda x: "" if pd.isna(x) else str(x).strip())

    for col in ["WithdrawalAmt", "DepositAmt", "ClosingBalance"]:
        tx[col] = tx[col].apply(lambda x: "" if pd.isna(x) else str(x).strip()).str.replace(",", "", regex=False)

    tx["WithdrawalAmount"] = pd.to_numeric(tx["WithdrawalAmt"], errors="coerce").fillna(0.0)
    tx["DepositAmount"] = pd.to_numeric(tx["DepositAmt"], errors="coerce").fillna(0.0)

    if (tx["WithdrawalAmount"] < 0).any() or (tx["DepositAmount"] < 0).any():
        tx["WithdrawalAmount"] = tx["WithdrawalAmount"].abs()
        tx["DepositAmount"] = tx["DepositAmount"].abs()

    tx["SignedAmount"] = tx["DepositAmount"] - tx["WithdrawalAmount"]

    tx["DescriptionRaw"] = tx["Narration"]
    tx["DescriptionNormalized"] = tx["Narration"].apply(normalize_description)

    # Extract AccountLast4 for fingerprinting
    header_block = raw.iloc[:header_row].astype(str)
    flat_text = "\n".join(
        " ".join([c for c in row.tolist() if c and c != "nan"])
        for _, row in header_block.iterrows()
    )
    account_last4 = extract_account_last4_from_header(flat_text)

    def make_fingerprint(row):
        date_str = row["Date"].date().isoformat() if pd.notna(row["Date"]) else ""
        return compute_transaction_fingerprint(
            institution=institution,
            account_last4=account_last4,
            date_str=date_str,
            description_normalized=row["DescriptionNormalized"],
            reference_number=row["RefNo"],
            signed_amount=row["SignedAmount"],
            closing_balance=row["ClosingBalance"],
        )

    tx["TransactionFingerprint"] = tx.apply(make_fingerprint, axis=1)
    tx["TransactionId"] = [str(uuid.uuid4()) for _ in range(len(tx))]
    tx["SourceFileId"] = source_file_id
    tx["PostedDate"] = ""
    tx["ReferenceNumber"] = tx["RefNo"]
    tx["CreatedAt"] = now
    tx["UpdatedAt"] = now

    result_tx = tx[MASTER_LEDGER_COLUMNS].copy()

    # Extract statement metadata
    meta = extract_statement_metadata(raw, header_row, result_tx, institution)
    return result_tx, meta


# ---------------------------------------------------------------------------
# Master ledger management
# ---------------------------------------------------------------------------

def safe_replace_with_backup(df: pd.DataFrame, final_path: str):
    """Write CSV safely: write .tmp → backup existing with timestamp → swap in new file."""
    out = Path(final_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")

    try:
        df.to_csv(tmp, index=False, encoding='utf-8')
        if out.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup = out.with_name(f"{out.stem}.backup_{timestamp}{out.suffix}")
            out.replace(backup)
        tmp.replace(out)
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"Failed to write {final_path}: {e}")


def update_master_ledger(
    master_path: str, new_rows: pd.DataFrame
) -> Tuple[pd.DataFrame, int, int]:
    """Merge new_rows into master ledger using TransactionFingerprint for de-dupe.

    Returns (updated_df, rows_added, rows_skipped).
    """
    master_file = Path(master_path)
    master_file.parent.mkdir(parents=True, exist_ok=True)

    if master_file.exists() and master_file.stat().st_size > 0:
        existing = pd.read_csv(master_file, dtype=str, encoding='utf-8').fillna("")
        if "Date" in existing.columns:
            existing["Date"] = parse_date_series(existing["Date"])
    else:
        existing = pd.DataFrame(columns=MASTER_LEDGER_COLUMNS)

    intra_batch_dupes = new_rows["TransactionFingerprint"].duplicated()
    if intra_batch_dupes.any():
        new_rows = new_rows[~intra_batch_dupes].copy()

    if not existing.empty:
        required_cols = ["TransactionFingerprint"]
        missing = [c for c in required_cols if c not in existing.columns]
        if missing:
            raise ValueError(f"master_ledger.csv missing required columns: {missing}")
        existing_fingerprints = set(existing["TransactionFingerprint"].tolist())
    else:
        existing_fingerprints = set()
    is_new = ~new_rows["TransactionFingerprint"].isin(existing_fingerprints)
    rows_added = int(is_new.sum())
    rows_skipped = int((~is_new).sum())

    new_rows_to_add = new_rows[is_new].copy().astype(str).fillna("")
    if "Date" in new_rows_to_add.columns:
        new_rows_to_add["Date"] = parse_date_series(new_rows_to_add["Date"])
    combined = pd.concat([existing, new_rows_to_add], ignore_index=True)
    keep_cols = [c for c in MASTER_LEDGER_COLUMNS if c in combined.columns]
    combined = combined[keep_cols].reset_index(drop=True)

    safe_replace_with_backup(combined, str(master_file))
    return combined, rows_added, rows_skipped


# ---------------------------------------------------------------------------
# Ingestion tracking (uploaded_files.csv and ingestion_runs.csv)
# ---------------------------------------------------------------------------

def append_uploaded_file_row(
    source_file_id: str,
    original_filename: str,
    input_path: str,
    source_type: str,
    institution: str,
    statement_metadata: Dict[str, str],
    rows_parsed: int = 0,
    rows_added: int = 0,
) -> None:
    """Append one row to uploaded_files.csv."""
    file_hash = compute_file_hash(input_path)
    now = datetime.now(timezone.utc).isoformat()

    parsed_status = "PARSED" if rows_parsed > 0 else "PARSE_FAILED"
    validation_status = "PASSED" if rows_parsed > 0 and rows_added > 0 else ("PASSED_DUPLICATES" if rows_parsed > 0 else "FAILED")

    row_data = {
        "SourceFileId": source_file_id,
        "OriginalFileName": original_filename,
        "StoredFilePath": input_path,
        "FileHash": file_hash,
        "SourceType": source_type,
        "Institution": institution,
        "AccountOrCardLast4": statement_metadata.get("AccountOrCardLast4", ""),
        "StatementPeriodStart": statement_metadata.get("StatementPeriodStart", ""),
        "StatementPeriodEnd": statement_metadata.get("StatementPeriodEnd", ""),
        "TotalTransactionsInFile": statement_metadata.get("TotalTransactionsInFile", 0),
        "UploadedAt": now,
        "ParsedStatus": parsed_status,
        "ValidationStatus": validation_status,
    }

    row_df = pd.DataFrame([row_data])
    row_df = row_df.astype(str).fillna("")

    out_file = Path(UPLOADED_FILES_PATH)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists() and out_file.stat().st_size > 0:
        existing = pd.read_csv(out_file, dtype=str, encoding='utf-8').fillna("")
        combined = pd.concat([existing, row_df], ignore_index=True)
    else:
        combined = row_df

    combined = combined[UPLOADED_FILES_COLUMNS]
    safe_replace_with_backup(combined, str(out_file))


def append_ingestion_run_row(
    run_id: str,
    source_file_id: str,
    rows_parsed: int,
    rows_added: int,
    rows_skipped: int,
    rows_failed_validation: int = 0,
) -> None:
    """Append one row to ingestion_runs.csv."""
    now = datetime.now(timezone.utc).isoformat()

    status = "SUCCESS" if rows_added > 0 else ("DUPLICATE_SKIP" if rows_skipped > 0 else "FAILED")

    row_data = {
        "RunId": run_id,
        "SourceFileId": source_file_id,
        "RowsParsed": rows_parsed,
        "RowsAdded": rows_added,
        "RowsSkippedAsDuplicates": rows_skipped,
        "RowsFailedValidation": rows_failed_validation,
        "StartedAt": now,
        "CompletedAt": now,
        "Status": status,
    }

    row_df = pd.DataFrame([row_data])
    row_df = row_df.astype(str).fillna("")

    out_file = Path(INGESTION_RUNS_PATH)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists() and out_file.stat().st_size > 0:
        existing = pd.read_csv(out_file, dtype=str, encoding='utf-8').fillna("")
        combined = pd.concat([existing, row_df], ignore_index=True)
    else:
        combined = row_df

    combined = combined[INGESTION_RUNS_COLUMNS]
    safe_replace_with_backup(combined, str(out_file))


def compute_categorization_confidence(row: pd.Series) -> str:
    """Determine confidence level based on categorization signals."""
    if row.get("Category") == "Uncategorized":
        return "NONE"
    if row.get("NeedsReview"):
        return "LOW"
    if row.get("MatchedPattern"):
        return "HIGH"
    return "MEDIUM"


def write_enriched_ledger(
    categorized_df: pd.DataFrame,
    source_file_id: str,
    source_type: str,
    institution: str,
    original_filename: str,
    statement_metadata: Dict[str, str],
) -> None:
    """Write enriched_ledger.csv (rebuilt from master_ledger + uploaded_files metadata + categorization).

    Enriched ledger is fully derived: it joins master_ledger with uploaded_files to get
    correct source metadata for all transactions (not just the current run).
    Parameters source_type, institution, original_filename, statement_metadata retained for
    backwards compatibility but data comes from uploaded_files.csv.
    """
    out_file = Path(ENRICHED_LEDGER_PATH)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    master_file = Path(MASTER_LEDGER_PATH)
    uploaded_file = Path(UPLOADED_FILES_PATH)

    if not master_file.exists() or not uploaded_file.exists():
        return

    master = pd.read_csv(master_file, dtype=str, encoding='utf-8').fillna("")
    uploaded = pd.read_csv(uploaded_file, dtype=str, encoding='utf-8').fillna("")

    required_master_cols = ["TransactionId", "Date", "DescriptionNormalized", "SourceFileId"]
    missing_master = [c for c in required_master_cols if c not in master.columns]
    if missing_master:
        raise ValueError(f"master_ledger.csv missing required columns: {missing_master}")

    required_uploaded_cols = ["SourceFileId", "SourceType", "Institution"]
    missing_uploaded = [c for c in required_uploaded_cols if c not in uploaded.columns]
    if missing_uploaded:
        raise ValueError(f"uploaded_files.csv missing required columns: {missing_uploaded}")

    if uploaded["SourceFileId"].duplicated().any():
        raise ValueError("uploaded_files.csv contains duplicate SourceFileId values. Data integrity check failed.")

    if "Date" in master.columns:
        master["Date"] = parse_date_series(master["Date"])

    merged = master.merge(
        uploaded[["SourceFileId", "SourceType", "Institution", "AccountOrCardLast4", "StatementPeriodStart", "StatementPeriodEnd", "OriginalFileName"]],
        on="SourceFileId", how="left"
    )
    merged = merged.rename(columns={"OriginalFileName": "SourceFileName"})

    if pd.api.types.is_datetime64_any_dtype(merged["Date"]):
        merged["Date"] = merged["Date"].dt.strftime("%Y-%m-%d")

    categorization_cols = ["TransactionId", "Category", "Subcategory", "Merchant", "MatchedPattern", "NeedsReview", "ReviewReason", "IsReversal", "ReversalGroupId", "Flow", "PaymentMode", "CounterpartyGuess", "UPIHandle", "TxnIdGuess"]
    categorized_for_merge = categorized_df[categorization_cols].copy()

    merged = merged.merge(categorized_for_merge, on="TransactionId", how="left")
    merged["CategorizationConfidence"] = merged[["Category", "NeedsReview", "MatchedPattern"]].apply(compute_categorization_confidence, axis=1)

    enriched = merged[[c for c in ENRICHED_LEDGER_COLUMNS if c in merged.columns]].copy()

    if "NeedsReview" in enriched.columns:
        enriched["NeedsReview"] = enriched["NeedsReview"].astype(bool).astype(int).astype(str)
    if "IsReversal" in enriched.columns:
        enriched["IsReversal"] = enriched["IsReversal"].astype(bool).astype(int).astype(str)

    enriched = enriched.astype(str).fillna("")

    safe_replace_with_backup(enriched, str(out_file))


# ---------------------------------------------------------------------------
# Enrichment (derives reporting fields from master_ledger data)
# ---------------------------------------------------------------------------

def enrich_master_ledger(master_df: pd.DataFrame) -> pd.DataFrame:
    """Add derived fields for categorization and reporting."""
    df = master_df.copy()

    if not pd.api.types.is_datetime64_any_dtype(df["Date"]):
        df["Date"] = parse_date_series(df["Date"])

    if df["Date"].isna().any():
        invalid_date_count = df["Date"].isna().sum()
        if "NeedsReview" not in df.columns:
            df["NeedsReview"] = False
        if "ReviewReason" not in df.columns:
            df["ReviewReason"] = ""
        df.loc[df["Date"].isna(), "NeedsReview"] = True
        df.loc[df["Date"].isna() & df["ReviewReason"].eq(""), "ReviewReason"] = "NO_DATE"

    for col in ["WithdrawalAmount", "DepositAmount", "SignedAmount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["Flow"] = df["SignedAmount"].apply(
        lambda x: "INFLOW" if x > 0 else ("OUTFLOW" if x < 0 else "NEUTRAL")
    )

    derived = df["DescriptionNormalized"].apply(extract_narration_features).apply(pd.Series)
    df = pd.concat([df, derived], axis=1)

    df["Year"] = df["Date"].dt.year
    df["Month"] = df["Date"].dt.to_period("M").astype(str)
    df["Day"] = df["Date"].dt.day_name()
    return df


def extract_narration_features(narration: str) -> dict:
    text = (narration or "").strip()
    parts = [p.strip() for p in text.split("-") if p.strip()]
    upi_match = re.search(r"([A-Za-z0-9._-]+@[A-Za-z0-9._-]+)", text)
    txn_match = re.search(r"\b(\d{10,20})\b", text)
    return {
        "PaymentMode": parts[0].upper() if parts else "OTHER",
        "CounterpartyGuess": parts[1] if len(parts) > 1 else "",
        "UPIHandle": upi_match.group(1) if upi_match else "",
        "TxnIdGuess": txn_match.group(1) if txn_match else "",
    }


def normalize_merchant_key(narration: str) -> str:
    """Stable key from narration for reversal pairing (strips ids, numbers)."""
    s = re.sub(r"\b\d{4,}\b", " ", (narration or "").upper())
    s = re.sub(r"[^A-Z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:60]


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------

def load_mapping(mapping_path: str) -> list:
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("rules", [])
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Category mapping file not found: {mapping_path}\n"
            f"Expected location: {Path(mapping_path).resolve()}\n"
            f"Create a category mapping JSON file with a 'rules' array."
        )
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {mapping_path}: {e}")


def categorize_with_mapping(df: pd.DataFrame, rules: list) -> pd.DataFrame:
    out = df.copy()
    out["Category"] = "Uncategorized"
    out["Subcategory"] = ""
    out["Merchant"] = ""
    out["MatchedPattern"] = ""
    out["NeedsReview"] = False
    out["ReviewReason"] = ""

    narr_upper = out["DescriptionNormalized"].fillna("").str.upper()
    patterns = [str(r.get("pattern", "")).upper().strip() for r in rules if r.get("pattern")]

    out["AllMatchedPatterns"] = out["DescriptionNormalized"].apply(
        lambda n: [p for p in patterns if p and p in (n or "").upper()]
    )
    multi_mask = out["AllMatchedPatterns"].apply(lambda xs: len(xs) > 1)
    out.loc[multi_mask, "NeedsReview"] = True
    out.loc[multi_mask, "ReviewReason"] = "MULTIPLE_MATCHES"

    for rule in rules:
        pattern = str(rule.get("pattern", "")).upper().strip()
        if not pattern:
            continue
        mask = out["Category"].eq("Uncategorized") & narr_upper.str.contains(re.escape(pattern), regex=True)
        out.loc[mask, "Category"] = rule.get("category", "Uncategorized")
        out.loc[mask, "Subcategory"] = rule.get("subcategory", "")
        out.loc[mask, "Merchant"] = rule.get("merchant", "")
        out.loc[mask, "MatchedPattern"] = pattern

    no_match = out["Category"].eq("Uncategorized")
    out.loc[no_match, "NeedsReview"] = True
    out.loc[no_match & out["ReviewReason"].eq(""), "ReviewReason"] = "NO_MATCH"

    out["MerchantKey"] = out.apply(
        lambda r: str(r.get("MatchedPattern", "")).strip() or normalize_merchant_key(r.get("DescriptionNormalized", "")),
        axis=1,
    )
    return out


# ---------------------------------------------------------------------------
# Reversal detection
# ---------------------------------------------------------------------------

def detect_reversal_pairs(df: pd.DataFrame, day_window: int = 7, amount_tolerance: float = 0.01) -> pd.DataFrame:
    """Pair likely reversal/refund transactions by MerchantKey, opposite sign, within day_window.

    Stricter criteria: amount_tolerance reduced to 0.01 to avoid pairing unrelated transactions.
    """
    out = df.copy().reset_index(drop=True)
    out["IsReversal"] = False
    out["ReversalGroupId"] = ""
    out["ReversalPairWithRefNo"] = ""
    out["Tag"] = ""

    if out.empty:
        return out

    work = out.reset_index(drop=False).rename(columns={"index": "_row_pos"})
    work["_abs_amount"] = work["SignedAmount"].abs()
    work = work.sort_values(["MerchantKey", "_abs_amount", "Date", "_row_pos"]).reset_index(drop=True)
    used: set = set()

    for _key, g in work[work["MerchantKey"].str.strip() != ""].groupby("MerchantKey", sort=False):
        if len(g) < 2:
            continue
        rows = g.to_dict("records")
        for i in range(len(rows)):
            a = rows[i]
            if a["_row_pos"] in used:
                continue
            for j in range(i + 1, len(rows)):
                b = rows[j]
                if b["_row_pos"] in used:
                    continue
                if (a["SignedAmount"] == 0) or (b["SignedAmount"] == 0):
                    continue
                if (a["SignedAmount"] > 0) == (b["SignedAmount"] > 0):
                    continue
                if abs(a["_abs_amount"] - b["_abs_amount"]) > amount_tolerance:
                    continue
                da, db = a["Date"], b["Date"]
                if pd.isna(da) or pd.isna(db) or abs((da - db).days) > day_window:
                    continue

                gid = str(uuid.uuid4())
                ai, bi = a["_row_pos"], b["_row_pos"]
                used.update([ai, bi])
                for pos, other_pos in [(ai, bi), (bi, ai)]:
                    out.loc[pos, "IsReversal"] = True
                    out.loc[pos, "ReversalGroupId"] = gid
                    out.loc[pos, "ReversalPairWithRefNo"] = str(out.loc[other_pos, "ReferenceNumber"])
                    out.loc[pos, "Tag"] = "REVERSAL_CANDIDATE"
                break

    out.loc[out["IsReversal"], "NeedsReview"] = True
    for pos in out[out["IsReversal"]].index:
        if out.loc[pos, "ReviewReason"]:
            out.loc[pos, "ReviewReason"] = out.loc[pos, "ReviewReason"] + "|REVERSAL_SUSPECTED"
        else:
            out.loc[pos, "ReviewReason"] = "REVERSAL_SUSPECTED"
    return out


# ---------------------------------------------------------------------------
# Summary and output
# ---------------------------------------------------------------------------

def create_summary(df: pd.DataFrame) -> pd.DataFrame:
    is_rev = df.get("IsReversal", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    spend = df[(df["SignedAmount"] < 0) & (~is_rev)].copy()
    spend["Expense"] = spend["SignedAmount"].abs()
    return (
        spend.groupby(["Month", "Category", "Subcategory"], dropna=False, as_index=False)["Expense"]
        .sum()
        .sort_values(["Month", "Expense"], ascending=[True, False])
    )


def save_outputs(categorized: pd.DataFrame, summary: pd.DataFrame, output_path: str, summary_path: str):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        categorized.to_excel(writer, index=False, sheet_name="transactions")
        categorized[categorized["NeedsReview"]].to_excel(writer, index=False, sheet_name="review_queue")
        is_rev = categorized.get("IsReversal", pd.Series(False, index=categorized.index)).fillna(False).astype(bool)
        categorized[is_rev].to_excel(writer, index=False, sheet_name="reversal_candidates")
        pd.DataFrame({
            "total_rows": [len(categorized)],
            "uncategorized_rows": [int((categorized["Category"] == "Uncategorized").sum())],
            "coverage_pct": [
                round(100.0 * (1.0 - (categorized["Category"] == "Uncategorized").mean()), 2)
                if len(categorized) else 0.0
            ],
            "needs_review_rows": [int(categorized["NeedsReview"].sum())],
            "reversal_rows": [int(categorized.get("IsReversal", pd.Series(False)).sum())],
        }).to_excel(writer, index=False, sheet_name="qa_summary")

    Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
    summary.to_excel(summary_path, index=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Categorize bank statement transactions.")
    parser.add_argument("--input", required=True, help="Path to a single statement file (.xls/.xlsx)")
    parser.add_argument("--institution", required=True, help="Institution name (e.g., HDFC, ICICI)")
    parser.add_argument("--source-type", required=True, help="Source type (e.g., Bank Account, Credit Card)")
    args = parser.parse_args()

    original_filename = Path(args.input).name
    file_hash = compute_file_hash(args.input)

    uploaded_file = Path(UPLOADED_FILES_PATH)
    if uploaded_file.exists() and uploaded_file.stat().st_size > 0:
        existing_uploads = pd.read_csv(uploaded_file, dtype=str, encoding='utf-8')
        duplicate = existing_uploads[
            (existing_uploads["FileHash"] == file_hash) &
            (existing_uploads["ParsedStatus"] == "PARSED")
        ]
        if not duplicate.empty:
            print(f"File already processed: {original_filename} (hash: {file_hash[:16]}...)")
            print(f"Skipping re-ingestion.")
            return

    source_file_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    print(f"Parsing: {args.input}")
    tx_new, statement_meta = parse_statement(args.input, source_file_id, args.institution, args.source_type)
    rows_parsed = len(tx_new)
    print(f"Parsed rows: {rows_parsed}")

    tx_master, rows_added, rows_skipped = update_master_ledger(MASTER_LEDGER_PATH, tx_new)
    print(f"Rows added to master ledger: {rows_added}")
    print(f"Rows skipped (duplicates): {rows_skipped}")
    print(f"Master ledger total: {len(tx_master)}")

    # Record file upload metadata
    append_uploaded_file_row(
        source_file_id=source_file_id,
        original_filename=original_filename,
        input_path=args.input,
        source_type=args.source_type,
        institution=args.institution,
        statement_metadata=statement_meta,
        rows_parsed=rows_parsed,
        rows_added=rows_added,
    )
    print(f"Recorded in uploaded_files.csv")

    # Record ingestion run stats
    append_ingestion_run_row(
        run_id=run_id,
        source_file_id=source_file_id,
        rows_parsed=rows_parsed,
        rows_added=rows_added,
        rows_skipped=rows_skipped,
    )
    print(f"Recorded in ingestion_runs.csv")

    if tx_master.empty:
        raise ValueError("No transactions in master ledger.")

    rules = load_mapping(MAPPING_PATH)
    enriched = enrich_master_ledger(tx_master)
    categorized = categorize_with_mapping(enriched, rules)
    categorized = detect_reversal_pairs(categorized)

    # Write enriched ledger (denormalized view with all metadata + categorization)
    write_enriched_ledger(
        categorized_df=categorized,
        source_file_id=source_file_id,
        source_type=args.source_type,
        institution=args.institution,
        original_filename=original_filename,
        statement_metadata=statement_meta,
    )
    print(f"Recorded in enriched_ledger.csv")

    summary = create_summary(categorized)
    save_outputs(categorized, summary, OUTPUT_PATH, SUMMARY_PATH)

    print(f"Done. Rows processed: {len(categorized)}")
    print(f"Uncategorized: {int((categorized['Category'] == 'Uncategorized').sum())}")
    print(f"Output: {OUTPUT_PATH}")
    print(f"Summary: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
