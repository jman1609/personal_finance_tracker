import argparse
import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path

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


def find_header_row(df: pd.DataFrame) -> int:
    for idx, row in df.iterrows():
        line = " | ".join([str(v).strip().lower() for v in row.tolist() if pd.notna(v)])
        if all(k in line for k in HEADER_KEYWORDS):
            return idx
    raise ValueError("Could not find transaction header row in statement.")


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

    # Core derived fields from narration.
    derived = tx["Narration"].apply(extract_narration_features).apply(pd.Series)
    tx = pd.concat([tx, derived], axis=1)

    # Practical columns for downstream analysis.
    tx["Year"] = tx["Date"].dt.year
    tx["Month"] = tx["Date"].dt.to_period("M").astype(str)
    tx["Day"] = tx["Date"].dt.day_name()

    tx = tx.drop(columns=["ValueDate"])  # explicitly keep one date only

    return tx


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
    parser.add_argument("--input", required=True, help="Absolute path to input statement (.xls/.xlsx/.csv*)")
    parser.add_argument("--sheet", default="Sheet 1", help="Excel sheet name (default: Sheet 1)")
    parser.add_argument("--mapping", default="config/category_mapping.json", help="Mapping JSON file path")
    parser.add_argument(
        "--output", default="data/processed/categorized_transactions.xlsx", help="Output workbook path"
    )
    parser.add_argument("--summary", default="data/processed/category_summary.xlsx", help="Summary output path")
    args = parser.parse_args()

    tx = parse_statement(args.input, args.sheet)
    rules = load_mapping(args.mapping)
    categorized = categorize_with_mapping(tx, rules)
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
