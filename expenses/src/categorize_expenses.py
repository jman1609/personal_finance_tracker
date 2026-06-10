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
    """SHA-256 fingerprint for stable cross-run de-duplication."""
    parts = "|".join([
        institution.upper().strip(),
        account_last4,
        date_str,
        description_normalized.upper(),
        reference_number,
        f"{signed_amount:.4f}",
        closing_balance,
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

def find_header_row(df: pd.DataFrame) -> int:
    for idx, row in df.iterrows():
        line = " | ".join([str(v).strip().lower() for v in row.tolist() if pd.notna(v)])
        if all(k in line for k in HDFC_HEADER_KEYWORDS):
            return idx
    raise ValueError("Could not find transaction header row in statement.")


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
    account_no_match = re.search(r"Account\s*No\s*:?\s*(\d{8,})", flat_text, flags=re.IGNORECASE)
    account_no = account_no_match.group(1) if account_no_match else ""
    last4 = normalize_account_last4(account_no[-4:] if len(account_no) >= 4 else "")

    # Extract period from transaction dates
    if not tx_df.empty and "Date" in tx_df.columns:
        if pd.api.types.is_datetime64_any_dtype(tx_df["Date"]):
            dates = tx_df["Date"]
        else:
            dates = pd.to_datetime(tx_df["Date"], errors="coerce")
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
    header_row = find_header_row(raw)

    tx = raw.iloc[header_row + 2:].copy()  # +2 skips the ***** separator row
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
    tx["SignedAmount"] = tx["DepositAmount"] - tx["WithdrawalAmount"]

    tx["DescriptionRaw"] = tx["Narration"]
    tx["DescriptionNormalized"] = tx["Narration"].apply(normalize_description)

    # Extract AccountLast4 for fingerprinting
    header_block = raw.iloc[:header_row].astype(str)
    flat_text = "\n".join(
        " ".join([c for c in row.tolist() if c and c != "nan"])
        for _, row in header_block.iterrows()
    )
    account_no_match = re.search(r"Account\s*No\s*:?\s*(\d{8,})", flat_text, flags=re.IGNORECASE)
    account_no = account_no_match.group(1) if account_no_match else ""
    account_last4 = normalize_account_last4(account_no[-4:] if len(account_no) >= 4 else "")

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
    """Write CSV safely: write .tmp → backup existing → swap in new file."""
    out = Path(final_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    if out.exists():
        backup = out.with_name(out.stem + ".backup" + out.suffix)
        out.replace(backup)
    tmp.replace(out)


def update_master_ledger(
    master_path: str, new_rows: pd.DataFrame
) -> Tuple[pd.DataFrame, int, int]:
    """Merge new_rows into master ledger using TransactionFingerprint for de-dupe.

    Returns (updated_df, rows_added, rows_skipped).
    """
    master_file = Path(master_path)
    master_file.parent.mkdir(parents=True, exist_ok=True)

    if master_file.exists() and master_file.stat().st_size > 0:
        existing = pd.read_csv(master_file, dtype=str).fillna("")
        if "Date" in existing.columns:
            existing["Date"] = parse_date_series(existing["Date"])
    else:
        existing = pd.DataFrame(columns=MASTER_LEDGER_COLUMNS)

    existing_fingerprints = set(existing["TransactionFingerprint"].tolist()) if not existing.empty else set()
    is_new = ~new_rows["TransactionFingerprint"].isin(existing_fingerprints)
    rows_added = int(is_new.sum())
    rows_skipped = int((~is_new).sum())

    combined = pd.concat([existing, new_rows[is_new]], ignore_index=True)
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
) -> None:
    """Append one row to uploaded_files.csv."""
    file_hash = compute_file_hash(input_path)
    now = datetime.now(timezone.utc).isoformat()

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
        "ParsedStatus": "PARSED",
        "ValidationStatus": "PENDING",
    }

    row_df = pd.DataFrame([row_data])
    row_df = row_df.astype(str).fillna("")

    out_file = Path(UPLOADED_FILES_PATH)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists() and out_file.stat().st_size > 0:
        existing = pd.read_csv(out_file, dtype=str).fillna("")
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
) -> None:
    """Append one row to ingestion_runs.csv."""
    now = datetime.now(timezone.utc).isoformat()

    row_data = {
        "RunId": run_id,
        "SourceFileId": source_file_id,
        "RowsParsed": rows_parsed,
        "RowsAdded": rows_added,
        "RowsSkippedAsDuplicates": rows_skipped,
        "RowsFailedValidation": 0,
        "StartedAt": now,
        "CompletedAt": now,
        "Status": "SUCCESS",
    }

    row_df = pd.DataFrame([row_data])
    row_df = row_df.astype(str).fillna("")

    out_file = Path(INGESTION_RUNS_PATH)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists() and out_file.stat().st_size > 0:
        existing = pd.read_csv(out_file, dtype=str).fillna("")
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
    """Write categorized transactions to enriched_ledger.csv.

    Enriched ledger is a denormalized view combining master_ledger + file metadata + categorization.
    """
    out = categorized_df.copy()

    # Add metadata columns
    out["SourceType"] = source_type
    out["Institution"] = institution
    out["AccountOrCardLast4"] = statement_metadata.get("AccountOrCardLast4", "")
    out["StatementPeriodStart"] = statement_metadata.get("StatementPeriodStart", "")
    out["StatementPeriodEnd"] = statement_metadata.get("StatementPeriodEnd", "")
    out["SourceFileName"] = original_filename

    # Add confidence signal
    out["CategorizationConfidence"] = out.apply(compute_categorization_confidence, axis=1)

    # Keep only enriched_ledger schema columns
    out = out[[c for c in ENRICHED_LEDGER_COLUMNS if c in out.columns]]
    out = out.astype(str).fillna("")

    # Append to enriched_ledger.csv
    out_file = Path(ENRICHED_LEDGER_PATH)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists() and out_file.stat().st_size > 0:
        existing = pd.read_csv(out_file, dtype=str).fillna("")
        combined = pd.concat([existing, out], ignore_index=True)
    else:
        combined = out

    # De-dupe on TransactionId and keep latest
    if "TransactionId" in combined.columns:
        combined = combined.drop_duplicates(subset=["TransactionId"], keep="last").reset_index(drop=True)

    combined = combined[[c for c in ENRICHED_LEDGER_COLUMNS if c in combined.columns]]
    safe_replace_with_backup(combined, str(out_file))


# ---------------------------------------------------------------------------
# Enrichment (derives reporting fields from master_ledger data)
# ---------------------------------------------------------------------------

def enrich_master_ledger(master_df: pd.DataFrame) -> pd.DataFrame:
    """Add derived fields for categorization and reporting."""
    df = master_df.copy()

    if not pd.api.types.is_datetime64_any_dtype(df["Date"]):
        df["Date"] = parse_date_series(df["Date"])

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
    with open(mapping_path, "r", encoding="utf-8") as f:
        return json.load(f).get("rules", [])


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

def detect_reversal_pairs(df: pd.DataFrame, day_window: int = 7, amount_tolerance: float = 1.0) -> pd.DataFrame:
    """Pair likely reversal/refund transactions by MerchantKey, opposite sign, within day_window."""
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

    for _key, g in work.groupby("MerchantKey", sort=False):
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
    out.loc[out["IsReversal"] & out["ReviewReason"].eq(""), "ReviewReason"] = "REVERSAL_SUSPECTED"
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

    source_file_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    original_filename = Path(args.input).name

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
