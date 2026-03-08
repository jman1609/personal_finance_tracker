import argparse
import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


HEADER_KEYWORDS = [
    "date",
    "narration",
    "withdrawal amt.",
    "deposit amt.",
    "closing balance",
]

# Flexible date tokens (dd/mm/yy, dd-mm-yyyy, yyyy-mm-dd, etc.)
DATE_TOKEN_REGEX = re.compile(r"^\s*(\d{1,4}[-/]\d{1,2}[-/]\d{1,4})\s*$")

DEFAULT_MASTER_LEDGER_PATH = "data/processed/master_ledger.csv"


def find_header_row(df: pd.DataFrame) -> int:
    for idx, row in df.iterrows():
        line = " | ".join([str(v).strip().lower() for v in row.tolist() if pd.notna(v)])
        if all(k in line for k in HEADER_KEYWORDS):
            return idx
    raise ValueError("Could not find transaction header row in statement.")


def extract_statement_metadata(raw: pd.DataFrame, header_row: int, source_path: str) -> Dict[str, str]:
    """Extract account metadata from rows above the transaction header.

    Works for the HDFC export format where account details appear in the first ~20 rows.
    """
    header_block = raw.iloc[:header_row].astype(str)
    flat_text = "\n".join(
        " ".join([c for c in row.tolist() if c and c != "nan"]) for _, row in header_block.iterrows()
    )

    account_no_match = re.search(r"Account\s*No\s*:?\s*(\d{8,})", flat_text, flags=re.IGNORECASE)
    account_no = account_no_match.group(1) if account_no_match else ""
    last4 = account_no[-4:] if len(account_no) >= 4 else ""

    # Holder name: take the first non-empty cell that looks like a name line.
    # We keep it exactly as in the file (no normalization), per user preference.
    holder_raw = ""
    for _, row in header_block.iterrows():
        for cell in row.tolist():
            if not cell or cell == "nan":
                continue
            s = str(cell).strip()
            # heuristic: contains letters and at least one space, but not a label with ':'
            if ":" in s:
                continue
            if re.search(r"[A-Za-z]", s) and len(s) >= 6 and (" " in s):
                holder_raw = s
                break
        if holder_raw:
            break

    downloaded_on = extract_download_date_from_filename(Path(source_path).name) or ""

    return {
        "AccountHolderRaw": holder_raw,
        "AccountNumber": account_no,
        "AccountLast4": last4,
        "DownloadedOn": downloaded_on,
        "SourceFile": Path(source_path).name,
    }


def extract_download_date_from_filename(filename: str) -> str:
    """Extract download date from filenames like `..._08032026.xls` -> 2026-03-08.

    Returns ISO date string (YYYY-MM-DD) or empty string.
    """
    m = re.search(r"_(\d{2})(\d{2})(\d{4})\.(xls|xlsx)$", filename, flags=re.IGNORECASE)
    if not m:
        return ""
    dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
    dt = pd.to_datetime(f"{dd}/{mm}/{yyyy}", dayfirst=True, errors="coerce")
    return dt.date().isoformat() if pd.notna(dt) else ""


def looks_like_date(value) -> bool:
    """Return True if a value can reasonably be interpreted as a date.

    Note: bank exports sometimes contain actual datetime objects OR Excel serials.
    This function is intentionally permissive to avoid dropping valid transaction rows.
    """
    if pd.isna(value):
        return False

    # Already date-like
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return True

    # Excel serial dates can come through as numbers
    if isinstance(value, (int, float)):
        dt = pd.to_datetime(value, unit="D", origin="1899-12-30", errors="coerce")
        return pd.notna(dt)

    # General string parse (dayfirst handles dd/mm/yy and dd-mm-yyyy cases)
    s = str(value).strip()
    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    return pd.notna(dt)


def parse_statement(input_path: str, sheet_name: str = "Sheet 1") -> pd.DataFrame:
    raw = pd.read_excel(input_path, sheet_name=sheet_name, header=None)
    header_row = find_header_row(raw)
    meta = extract_statement_metadata(raw, header_row, input_path)

    tx = raw.iloc[header_row + 2 :].copy()  # skip separator row of *****
    tx = tx.iloc[:, :7]
    tx.columns = [
        "Date",
        "Narration",
        "RefNo",
        "ValueDate",
        "WithdrawalAmt",
        "DepositAmt",
        "ClosingBalance",
    ]

    # Keep rows where first column resembles a date in any common format.
    tx = tx[tx["Date"].apply(looks_like_date)].copy()

    # Keep only one date column (Date). Drop ValueDate as requested.
    tx["Date"] = pd.to_datetime(tx["Date"].astype(str).str.strip(), errors="coerce", dayfirst=True)

    for col in ["WithdrawalAmt", "DepositAmt", "ClosingBalance"]:
        tx[col] = pd.to_numeric(tx[col], errors="coerce").fillna(0.0)

    tx["Narration"] = tx["Narration"].astype(str).str.strip()
    tx["Amount"] = tx["DepositAmt"] - tx["WithdrawalAmt"]
    tx["Flow"] = tx["Amount"].apply(lambda x: "INFLOW" if x > 0 else ("OUTFLOW" if x < 0 else "NEUTRAL"))

    # Attach statement-level metadata to every transaction row
    for k, v in meta.items():
        tx[k] = v

    # Core derived fields from narration.
    derived = tx["Narration"].apply(extract_narration_features).apply(pd.Series)
    tx = pd.concat([tx, derived], axis=1)

    # Practical columns for downstream analysis.
    tx["Year"] = tx["Date"].dt.year
    tx["Month"] = tx["Date"].dt.to_period("M").astype(str)
    tx["Day"] = tx["Date"].dt.day_name()

    tx = tx.drop(columns=["ValueDate"])  # explicitly keep one date only

    return tx


def list_input_files(input_path: Optional[str], input_dir: Optional[str]) -> List[str]:
    if input_path:
        return [input_path]
    if not input_dir:
        raise ValueError("Provide either --input or --input-dir")

    p = Path(input_dir)
    if not p.exists():
        raise ValueError(f"Input dir does not exist: {input_dir}")

    patterns = ["Acct_Statement_*.xls", "Acct_Statement_*.xlsx", "Statement_*.xls", "Statement_*.xlsx"]
    files: List[Path] = []
    for pat in patterns:
        files.extend(list(p.glob(pat)))
    files = sorted({f.resolve() for f in files})
    return [str(f) for f in files]


def compute_dedupe_key(df: pd.DataFrame) -> pd.Series:
    ref = df.get("RefNo")
    has_ref = ref.notna() & (ref.astype(str).str.strip() != "")

    key_primary = (
        df.get("AccountLast4", "").astype(str).fillna("")
        + "|"
        + ref.astype(str).fillna("")
    )

    key_fallback = (
        df.get("AccountLast4", "").astype(str).fillna("")
        + "|"
        + df.get("Date").astype(str).fillna("")
        + "|"
        + df.get("Amount").astype(str).fillna("")
        + "|"
        + df.get("Narration").astype(str).fillna("")
    )

    return key_primary.where(has_ref, key_fallback)


def safe_write_csv(df: pd.DataFrame, path: str):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(out)


def update_master_ledger(master_path: str, new_rows: pd.DataFrame) -> pd.DataFrame:
    master_file = Path(master_path)
    master_file.parent.mkdir(parents=True, exist_ok=True)

    if master_file.exists():
        existing = pd.read_csv(master_file)
    else:
        existing = pd.DataFrame()

    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined["DedupeKey"] = compute_dedupe_key(combined)
    combined = combined.drop_duplicates(subset=["DedupeKey"], keep="last").reset_index(drop=True)

    # backup then atomic write
    if master_file.exists():
        backup = master_file.with_name(master_file.stem + ".backup" + master_file.suffix)
        master_file.replace(backup)
        # after backup, write new
        safe_write_csv(combined, str(master_file))
    else:
        safe_write_csv(combined, str(master_file))

    return combined


def extract_narration_features(narration: str) -> dict:
    text = (narration or "").strip()
    parts = [p.strip() for p in text.split("-") if p.strip()]

    mode = parts[0].upper() if parts else "OTHER"
    counterparty = parts[1] if len(parts) > 1 else ""

    upi_handle_match = re.search(r"([A-Za-z0-9._-]+@[A-Za-z0-9._-]+)", text)
    txn_id_match = re.search(r"\b(\d{10,20})\b", text)

    return {
        "PaymentMode": mode,
        "CounterpartyGuess": counterparty,
        "UPIHandle": upi_handle_match.group(1) if upi_handle_match else "",
        "TxnIdGuess": txn_id_match.group(1) if txn_id_match else "",
    }


def normalize_merchant_key(narration: str) -> str:
    """Best-effort stable key from narration, used for reversals/refunds pairing."""
    s = (narration or "").upper()
    # Remove long numbers/ids to reduce noise.
    s = re.sub(r"\b\d{4,}\b", " ", s)
    # Remove common separators and collapse whitespace.
    s = re.sub(r"[^A-Z]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:60]


def load_mapping(mapping_path: str) -> list:
    with open(mapping_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("rules", [])


def categorize_with_mapping(df: pd.DataFrame, rules: list) -> pd.DataFrame:
    out = df.copy()
    out["Category"] = "Uncategorized"
    out["Subcategory"] = ""
    out["Merchant"] = ""
    out["MatchedPattern"] = ""
    out["NeedsReview"] = False
    out["ReviewReason"] = ""

    narr_upper = out["Narration"].fillna("").str.upper()
    # If multiple patterns match a single narration, that's a signal to review.
    patterns = []
    for rule in rules:
        pat = str(rule.get("pattern", "")).upper().strip()
        if pat:
            patterns.append(pat)

    def matched_patterns(narr: str) -> list:
        n = (narr or "").upper()
        return [p for p in patterns if p and (p in n)]

    out["AllMatchedPatterns"] = out["Narration"].apply(matched_patterns)
    multi_mask = out["AllMatchedPatterns"].apply(lambda xs: len(xs) > 1)
    out.loc[multi_mask, "NeedsReview"] = True
    out.loc[multi_mask, "ReviewReason"] = "MULTIPLE_MATCHES"

    # Apply rules in order for deterministic assignment (first match wins)
    for rule in rules:
        pattern = str(rule.get("pattern", "")).upper().strip()
        if not pattern:
            continue
        mask = (out["Category"] == "Uncategorized") & narr_upper.str.contains(re.escape(pattern), regex=True)
        out.loc[mask, "Category"] = rule.get("category", "Uncategorized")
        out.loc[mask, "Subcategory"] = rule.get("subcategory", "")
        out.loc[mask, "Merchant"] = rule.get("merchant", "")
        out.loc[mask, "MatchedPattern"] = pattern

    # No match -> review
    no_match_mask = out["Category"].eq("Uncategorized")
    out.loc[no_match_mask, "NeedsReview"] = True
    out.loc[no_match_mask & (out["ReviewReason"] == ""), "ReviewReason"] = "NO_MATCH"

    # MerchantKey for reversals pairing (prefer explicit mapping match)
    out["MerchantKey"] = out.apply(
        lambda r: (str(r.get("MatchedPattern", "")).strip() or normalize_merchant_key(r.get("Narration", ""))),
        axis=1,
    )

    return out


def detect_reversal_pairs(df: pd.DataFrame, day_window: int = 7, amount_tolerance: float = 1.0) -> pd.DataFrame:
    """Pair likely reversals/refunds.

    Heuristic: same MerchantKey, opposite sign, same abs(amount) within tolerance, within N days.
    We mark both rows and assign a shared ReversalGroupId.
    """
    out = df.copy()
    out["IsReversal"] = False
    out["ReversalGroupId"] = ""
    out["ReversalPairWithRefNo"] = ""
    out["Tag"] = ""

    if out.empty:
        return out

    # Work on indexed frame.
    work = out.reset_index(drop=False).rename(columns={"index": "_row_index"})
    work["_abs_amount"] = work["Amount"].abs()

    # Sort to make pairing deterministic.
    work = work.sort_values(["MerchantKey", "_abs_amount", "Date", "_row_index"]).reset_index(drop=True)
    used = set()

    # Pre-group by MerchantKey for speed.
    for key, g in work.groupby("MerchantKey", sort=False):
        if g.shape[0] < 2:
            continue
        rows = g.to_dict("records")
        # brute-force within group; groups are usually small
        for i in range(len(rows)):
            a = rows[i]
            ai = a["_row_index"]
            if ai in used:
                continue
            for j in range(i + 1, len(rows)):
                b = rows[j]
                bi = b["_row_index"]
                if bi in used:
                    continue
                # Opposite sign
                if (a["Amount"] == 0) or (b["Amount"] == 0) or (a["Amount"] > 0) == (b["Amount"] > 0):
                    continue
                # Amount match within tolerance
                if abs(a["_abs_amount"] - b["_abs_amount"]) > amount_tolerance:
                    continue
                # Date proximity
                da = a["Date"]
                db = b["Date"]
                if pd.isna(da) or pd.isna(db):
                    continue
                if abs((da - db).days) > day_window:
                    continue

                # Pair found
                gid = str(uuid.uuid4())
                used.add(ai)
                used.add(bi)
                out.loc[ai, "IsReversal"] = True
                out.loc[bi, "IsReversal"] = True
                out.loc[ai, "ReversalGroupId"] = gid
                out.loc[bi, "ReversalGroupId"] = gid
                out.loc[ai, "ReversalPairWithRefNo"] = str(out.loc[bi, "RefNo"])
                out.loc[bi, "ReversalPairWithRefNo"] = str(out.loc[ai, "RefNo"])
                break

    # Mark reversal suspected rows for review
    out.loc[out["IsReversal"], "NeedsReview"] = True
    out.loc[out["IsReversal"] & (out["ReviewReason"] == ""), "ReviewReason"] = "REVERSAL_SUSPECTED"
    out.loc[out["IsReversal"], "Tag"] = "REVERSAL_CANDIDATE"
    return out


def create_summary(df: pd.DataFrame) -> pd.DataFrame:
    # For spending summary, use absolute outflow values only and exclude likely reversals.
    spend = df[(df["Amount"] < 0) & (~df.get("IsReversal", False))].copy()
    spend["Expense"] = spend["Amount"].abs()

    summary = (
        spend.groupby(["Month", "Category", "Subcategory"], dropna=False, as_index=False)["Expense"]
        .sum()
        .sort_values(["Month", "Expense"], ascending=[True, False])
    )
    return summary


def save_outputs(categorized: pd.DataFrame, summary: pd.DataFrame, output_path: str, summary_path: str):
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        categorized.to_excel(writer, index=False, sheet_name="transactions")
        categorized[categorized["NeedsReview"]].to_excel(writer, index=False, sheet_name="review_queue")
        categorized[categorized.get("IsReversal", False)].to_excel(
            writer, index=False, sheet_name="reversal_candidates"
        )

        # Lightweight QA report
        qa = {
            "total_rows": [len(categorized)],
            "uncategorized_rows": [int((categorized["Category"] == "Uncategorized").sum())],
            "coverage_pct": [
                round(100.0 * (1.0 - (categorized["Category"] == "Uncategorized").mean()), 2)
                if len(categorized)
                else 0.0
            ],
            "needs_review_rows": [int(categorized["NeedsReview"].sum())],
            "reversal_rows": [int(categorized.get("IsReversal", pd.Series(False)).sum())],
        }
        pd.DataFrame(qa).to_excel(writer, index=False, sheet_name="qa_summary")

    sum_file = Path(summary_path)
    sum_file.parent.mkdir(parents=True, exist_ok=True)
    summary.to_excel(summary_path, index=False)


def main():
    parser = argparse.ArgumentParser(description="Categorize bank statement transactions with mapping-first logic.")
    parser.add_argument("--input", required=False, help="Path to a single input statement (.xls/.xlsx)")
    parser.add_argument("--input-dir", default=None, help="Directory containing downloaded statements (default: none)")
    parser.add_argument("--sheet", default="Sheet 1", help="Excel sheet name (default: Sheet 1)")
    parser.add_argument("--mapping", default="config/category_mapping.json", help="Mapping JSON file path")
    parser.add_argument("--master-ledger", default=DEFAULT_MASTER_LEDGER_PATH, help="Cumulative master ledger path")
    parser.add_argument(
        "--output", default="data/processed/categorized_transactions.xlsx", help="Output workbook path"
    )
    parser.add_argument("--summary", default="data/processed/category_summary.xlsx", help="Summary output path")
    args = parser.parse_args()

    input_files = list_input_files(args.input, args.input_dir)
    if not input_files:
        raise ValueError("No input files found. Provide --input or use --input-dir with matching files.")

    # Parse all input statements into a single dataframe
    parsed = [parse_statement(p, args.sheet) for p in input_files]
    tx_new = pd.concat(parsed, ignore_index=True) if parsed else pd.DataFrame()

    # Update master ledger (dedupe across runs) then rebuild outputs from it
    tx_master = update_master_ledger(args.master_ledger, tx_new) if not tx_new.empty else pd.DataFrame()

    if tx_master.empty:
        raise ValueError("No transactions found after parsing.")

    rules = load_mapping(args.mapping)
    categorized = categorize_with_mapping(tx_master, rules)
    categorized = detect_reversal_pairs(categorized)
    summary = create_summary(categorized)
    save_outputs(categorized, summary, args.output, args.summary)

    uncategorized_count = int((categorized["Category"] == "Uncategorized").sum())
    print(f"Done. Rows processed: {len(categorized)}")
    print(f"Uncategorized rows: {uncategorized_count}")
    print(f"Saved categorized workbook: {args.output}")
    print(f"Saved category summary: {args.summary}")


if __name__ == "__main__":
    main()
