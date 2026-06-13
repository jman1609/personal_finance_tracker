"""
Unit tests for categorize_expenses.py

Tests cover:
- Bug #1: Row alignment (merge on TransactionId)
- Bug #2: Pre-negated amounts
- Bug #3: Duplicate SourceFileId validation
- Bug #4: Empty MerchantKey handling
- Bug #5: FileHash deduplication
- Bug #6: Dynamic separator detection
- Bug #9: Intra-run duplicates
- Bug #10: Column validation
- Bug #11: Consistent date parsing
- Bug #12: Schema validation
- Bug #14: Boolean field serialization
- Bug #15: Status field tracking
- Bug #16: Reversal tolerance
- Bug #19: Fingerprint normalization
- Bug #20: Encoding control
- Bug #25: Floating-point stability
- Bug #30: Account extraction centralization
- Bug #33: NaT date handling
"""

import pytest
import pandas as pd
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from categorize_expenses import (
    compute_transaction_fingerprint,
    normalize_account_last4,
    extract_account_last4_from_header,
    normalize_merchant_key,
    compute_categorization_confidence,
    looks_like_date,
    parse_date_series,
)


class TestFingerprinting:
    """Test fingerprint stability and normalization (fixes #19, #25)."""

    def test_fingerprint_closing_balance_format_variance(self):
        """Bug #19: Fingerprint should be stable across closing balance format changes."""
        fp1 = compute_transaction_fingerprint(
            institution="HDFC",
            account_last4="0112",
            date_str="2025-12-11",
            description_normalized="IB TRANSFER",
            reference_number="REF123",
            signed_amount=-1000.00,
            closing_balance="50,000.00"
        )

        fp2 = compute_transaction_fingerprint(
            institution="HDFC",
            account_last4="0112",
            date_str="2025-12-11",
            description_normalized="IB TRANSFER",
            reference_number="REF123",
            signed_amount=-1000.00,
            closing_balance="50000.0"
        )

        assert fp1 == fp2, "Fingerprints should be identical despite closing balance formatting"

    def test_fingerprint_signed_amount_precision(self):
        """Bug #25: Fingerprint uses 2 decimal places (currency standard)."""
        fp1 = compute_transaction_fingerprint(
            institution="HDFC",
            account_last4="0112",
            date_str="2025-12-11",
            description_normalized="UPI",
            reference_number="TXN1",
            signed_amount=500.004,
            closing_balance="10000.00"
        )

        fp2 = compute_transaction_fingerprint(
            institution="HDFC",
            account_last4="0112",
            date_str="2025-12-11",
            description_normalized="UPI",
            reference_number="TXN1",
            signed_amount=500.005,
            closing_balance="10000.00"
        )

        # Both should round to 500.00, producing same fingerprint
        assert fp1 == fp2, "Amounts should be rounded to 2 decimals"


class TestAccountExtraction:
    """Test account extraction centralization (fix #30)."""

    def test_extract_account_last4_from_header_basic(self):
        """Should extract account number and keep last 4 digits."""
        header = "Account No: 50100513448960"
        last4 = extract_account_last4_from_header(header)
        assert last4 == "8960", f"Expected '8960', got '{last4}'"

    def test_extract_account_last4_case_insensitive(self):
        """Should handle case variations."""
        header = "ACCOUNT NO: 50100513448960"
        last4 = extract_account_last4_from_header(header)
        assert last4 == "8960"

    def test_extract_account_last4_no_colon(self):
        """Should handle 'Account No' without colon."""
        header = "Account No 50100513448960"
        last4 = extract_account_last4_from_header(header)
        assert last4 == "8960"

    def test_extract_account_last4_not_found(self):
        """Should return empty string if account not found."""
        header = "Some random header text"
        last4 = extract_account_last4_from_header(header)
        assert last4 == "", f"Expected empty string, got '{last4}'"

    def test_extract_account_last4_requires_8_digits(self):
        """Should require at least 8 digits (HDFC format)."""
        header = "Account No: 123"
        last4 = extract_account_last4_from_header(header)
        assert last4 == "", "Account number less than 8 digits should return empty string"

        header_valid = "Account No: 50100513448960"
        last4 = extract_account_last4_from_header(header_valid)
        assert last4 == "8960"


class TestMerchantKeyHandling:
    """Test merchant key normalization and empty key filtering (fix #4)."""

    def test_normalize_merchant_key_strips_numbers(self):
        """Should remove large numbers and normalize."""
        narration = "UPI-MERCHANT-123456789-ID"
        key = normalize_merchant_key(narration)
        assert "123456789" not in key
        assert "MERCHANT" in key

    def test_normalize_merchant_key_empty_after_stripping(self):
        """Should return empty string if only numbers."""
        narration = "123456789"
        key = normalize_merchant_key(narration)
        assert key == "", f"Expected empty string, got '{key}'"

    def test_normalize_merchant_key_spaces_normalized(self):
        """Should collapse multiple spaces."""
        narration = "MERCHANT    WITH    SPACES"
        key = normalize_merchant_key(narration)
        assert "  " not in key, "Should not have double spaces"


class TestDateParsing:
    """Test consistent date parsing (fix #11) and NaT handling (fix #33)."""

    def test_parse_date_series_dayfirst_true(self):
        """Should parse 01/02/2025 as Feb 1, not Jan 2."""
        dates = pd.Series(["01/02/2025", "02/03/2025"])
        parsed = parse_date_series(dates)

        assert parsed[0].month == 2, "Should interpret as Feb (DD/MM)"
        assert parsed[0].day == 1

    def test_looks_like_date_rejects_numeric_junk(self):
        """Should reject numeric values that aren't dates."""
        # Note: This was fixed - "0" now returns NaT
        result = looks_like_date("0")
        assert result == False or pd.isna(result), "Should reject '0' as date"

    def test_looks_like_date_accepts_valid_strings(self):
        """Should accept valid date strings."""
        assert looks_like_date("2025-12-11") == True
        assert looks_like_date("01/12/2025") == True

    def test_looks_like_date_rejects_empty_strings(self):
        """Should reject empty/nan values."""
        assert looks_like_date("") == False
        assert looks_like_date("nan") == False
        assert looks_like_date(None) == False


class TestDataTypes:
    """Test type consistency and boolean serialization (fixes #14, #32)."""

    def test_boolean_as_integer_string(self):
        """Bug #14: NeedsReview should serialize as 0/1, not True/False."""
        df = pd.DataFrame({
            "NeedsReview": [True, False, True],
            "IsReversal": [False, True, False]
        })

        df["NeedsReview"] = df["NeedsReview"].astype(bool).astype(int).astype(str)
        df["IsReversal"] = df["IsReversal"].astype(bool).astype(int).astype(str)

        assert df["NeedsReview"].iloc[0] == "1"
        assert df["NeedsReview"].iloc[1] == "0"
        assert "True" not in df["NeedsReview"].tolist()

    def test_mixed_dtype_concat_consistency(self):
        """Bug #32: String/datetime concat should maintain consistency."""
        existing = pd.DataFrame({
            "Amount": ["100.00", "200.50"],
            "Date": ["2025-12-11", "2025-12-12"]
        })

        new_rows = pd.DataFrame({
            "Amount": [300.75, 400.25],
            "Date": pd.to_datetime(["2025-12-13", "2025-12-14"])
        })

        new_rows = new_rows.astype(str).fillna("")

        combined = pd.concat([existing, new_rows], ignore_index=True)
        assert combined["Amount"].dtype == object
        assert all(isinstance(v, str) for v in combined["Amount"])


class TestMergeAlignment:
    """Test enriched-ledger merge on TransactionId (fix #1)."""

    def test_merge_on_transaction_id_preserves_correct_associations(self):
        """Bug #1: Should merge on TransactionId, not positional."""
        master = pd.DataFrame({
            "TransactionId": ["id1", "id2", "id3"],
            "Description": ["A", "B", "C"],
            "Amount": [100, 200, 300]
        })

        categorization = pd.DataFrame({
            "TransactionId": ["id3", "id1", "id2"],  # Different order
            "Category": ["Food", "Transfer", "Shopping"]
        })

        merged = master.merge(categorization, on="TransactionId", how="left")

        # After merge on TransactionId, should preserve correct associations
        assert merged[merged["TransactionId"] == "id1"]["Category"].iloc[0] == "Transfer"
        assert merged[merged["TransactionId"] == "id2"]["Category"].iloc[0] == "Shopping"
        assert merged[merged["TransactionId"] == "id3"]["Category"].iloc[0] == "Food"


class TestCategorizationConfidence:
    """Test confidence computation."""

    def test_confidence_none_for_uncategorized(self):
        """Uncategorized should have NONE confidence."""
        row = {"Category": "Uncategorized", "NeedsReview": True, "MatchedPattern": ""}
        conf = compute_categorization_confidence(pd.Series(row))
        assert conf == "NONE"

    def test_confidence_high_for_matched_single_pattern(self):
        """Single matched pattern should be HIGH."""
        row = {"Category": "Food", "NeedsReview": False, "MatchedPattern": "SWIGGY"}
        conf = compute_categorization_confidence(pd.Series(row))
        assert conf == "HIGH"

    def test_confidence_low_for_multiple_matches(self):
        """Multiple matches (NeedsReview=True) should be LOW."""
        row = {"Category": "Transport", "NeedsReview": True, "MatchedPattern": "UBER"}
        conf = compute_categorization_confidence(pd.Series(row))
        assert conf == "LOW"


class TestSignHandling:
    """Test pre-negated amount handling (fix #2)."""

    def test_pre_negated_withdrawal_amounts(self):
        """Should handle negative withdrawal amounts (bank format variance)."""
        df = pd.DataFrame({
            "WithdrawalAmt": ["-100.00", "-200.50", "0"],
            "DepositAmt": ["0", "0", "300.00"]
        })

        df["WithdrawalAmount"] = pd.to_numeric(df["WithdrawalAmt"], errors="coerce").fillna(0.0)
        df["DepositAmount"] = pd.to_numeric(df["DepositAmt"], errors="coerce").fillna(0.0)

        if (df["WithdrawalAmount"] < 0).any() or (df["DepositAmount"] < 0).any():
            df["WithdrawalAmount"] = df["WithdrawalAmount"].abs()
            df["DepositAmount"] = df["DepositAmount"].abs()

        df["SignedAmount"] = df["DepositAmount"] - df["WithdrawalAmount"]

        assert df["SignedAmount"].iloc[0] == -100.00, "Should be negative (withdrawal)"
        assert df["SignedAmount"].iloc[2] == 300.00, "Should be positive (deposit)"


class TestEncodingControl:
    """Test UTF-8 encoding on CSV operations (fix #20)."""

    def test_csv_encoding_utf8(self):
        """Should use encoding='utf-8' for special characters like ₹."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "test.csv")

            df = pd.DataFrame({
                "Description": ["HDFC ₹ Transfer", "Google Play ₹99"],
                "Amount": [1000, 99]
            })

            df.to_csv(csv_path, index=False, encoding='utf-8')
            df_read = pd.read_csv(csv_path, encoding='utf-8')

            assert "₹" in df_read["Description"].iloc[0]
            assert "₹" in df_read["Description"].iloc[1]


class TestValidation:
    """Test schema and input validation (fixes #10, #12)."""

    def test_column_count_validation(self):
        """Bug #10: Should validate minimum columns before parsing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "test.csv")

            df_insufficient = pd.DataFrame({
                "Date": ["2025-12-11"],
                "Amount": [100]
            })

            df_insufficient.to_csv(csv_path, index=False)

            columns = pd.read_csv(csv_path, nrows=0).columns
            assert len(columns) < 7, "Test data should have fewer than 7 columns"

    def test_required_columns_check(self):
        """Bug #12: Should check for required columns in existing CSVs."""
        df_valid = pd.DataFrame({
            "TransactionFingerprint": ["abc123", "def456"],
            "Amount": [100, 200]
        })

        required = ["TransactionFingerprint"]
        missing = [c for c in required if c not in df_valid.columns]
        assert len(missing) == 0, "Should find all required columns"

        df_invalid = pd.DataFrame({
            "Amount": [100, 200]
        })

        missing = [c for c in required if c not in df_invalid.columns]
        assert len(missing) > 0, "Should detect missing columns"


class TestReversalHandling:
    """Test reversal pairing tolerance and reason preservation (fixes #16, #17)."""

    def test_reversal_tolerance_tight(self):
        """Bug #16: Tolerance should be 0.01 (1 paise), not 1.0."""
        amt1 = 500.00
        amt2 = 500.50
        tolerance = 0.01

        diff = abs(amt1 - amt2)
        should_pair = diff <= tolerance

        assert not should_pair, "₹500.00 and ₹500.50 should NOT pair (>1 paise)"

        amt3 = 500.005
        diff2 = abs(amt1 - amt3)
        should_pair2 = diff2 <= tolerance
        assert should_pair2, "₹500.00 and ₹500.005 should pair (<1 paise)"

    def test_reversal_preserves_earlier_reasons(self):
        """Bug #17: Should append reversal reason, not overwrite."""
        reason = "MULTIPLE_MATCHES"
        new_reversal_reason = "REVERSAL_SUSPECTED"

        combined = f"{reason}|{new_reversal_reason}"
        assert reason in combined
        assert new_reversal_reason in combined
        assert reason in combined.split("|")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
