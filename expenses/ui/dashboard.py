import sys
import tempfile
from pathlib import Path
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from expenses.src.categorize_expenses import ingest_statement, rebuild_enriched_ledger

st.set_page_config(page_title="Expense Tracker", layout="wide")
st.title("Personal Finance Dashboard")

ENRICHED_LEDGER = "expenses/data/db/enriched_ledger.csv"
UPLOADED_FILES  = "expenses/data/db/uploaded_files.csv"


@st.cache_data
def load_data():
    df = pd.read_csv(ENRICHED_LEDGER, dtype=str)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["SignedAmount"] = pd.to_numeric(df["SignedAmount"], errors="coerce").fillna(0.0)
    df["IsReversal"] = df["IsReversal"].astype(str).isin(["1", "True", "true"])
    return df


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_dashboard, tab_upload, tab_history = st.tabs(["Dashboard", "Upload Statement", "Ingestion History"])


# ---------------------------------------------------------------------------
# Upload Statement
# ---------------------------------------------------------------------------
with tab_upload:
    st.subheader("Upload a Bank / Credit Card Statement")

    col1, col2 = st.columns(2)
    with col1:
        institution = st.selectbox("Institution", ["HDFC", "ICICI", "SBI", "Axis", "Kotak"])
    with col2:
        source_type = st.selectbox("Source Type", ["Bank Account", "Credit Card"])

    uploaded_file = st.file_uploader("Statement file (.xls or .xlsx)", type=["xls", "xlsx"])

    if uploaded_file and st.button("Ingest", type="primary"):
        original_name = uploaded_file.name
        suffix = Path(original_name).suffix  # .xls or .xlsx
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = tmp.name

        with st.spinner("Parsing and ingesting..."):
            try:
                result = ingest_statement(tmp_path, institution, source_type,
                                          original_filename=original_name)

                # Save a copy to raw/ before deleting the temp file
                raw_dir = Path("expenses/data/raw")
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_dest = raw_dir / original_name
                if not raw_dest.exists():
                    Path(tmp_path).rename(raw_dest)
                else:
                    Path(tmp_path).unlink(missing_ok=True)

                if result["skipped"]:
                    st.warning(f"**{result['filename']}** has already been processed. Skipping.")
                else:
                    st.success(f"**{result['filename']}** ingested successfully.")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Rows Parsed",   result["rows_parsed"])
                    c2.metric("Rows Added",    result["rows_added"])
                    c3.metric("Duplicates",    result["rows_skipped"])
                    c4.metric("Master Total",  result["master_total"])
                    st.info(f"Uncategorized: {result['uncategorized']} rows — go to Dashboard to review.")
                    st.cache_data.clear()

            except Exception as e:
                Path(tmp_path).unlink(missing_ok=True)
                st.error(f"Ingestion failed: {e}")


# ---------------------------------------------------------------------------
# Ingestion History
# ---------------------------------------------------------------------------
with tab_history:
    st.subheader("Uploaded Files")
    try:
        uploads = pd.read_csv(UPLOADED_FILES, dtype=str)
        display_cols = ["OriginalFileName", "Institution", "SourceType", "AccountOrCardLast4",
                        "StatementPeriodStart", "StatementPeriodEnd", "TotalTransactionsInFile",
                        "ParsedStatus", "UploadedAt"]
        st.dataframe(uploads[[c for c in display_cols if c in uploads.columns]],
                     use_container_width=True)
    except FileNotFoundError:
        st.info("No files ingested yet.")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
with tab_dashboard:
    try:
        df = load_data()
    except FileNotFoundError:
        st.info("No data yet. Upload a statement first.")
        st.stop()

    # Sidebar filters
    st.sidebar.header("Filters")

    accounts = sorted(df["AccountOrCardLast4"].dropna().unique().tolist())
    selected_accounts = st.sidebar.multiselect("Account", options=accounts, default=accounts)

    date_min, date_max = df["Date"].min(), df["Date"].max()
    date_range = st.sidebar.date_input("Date Range", value=(date_min, date_max),
                                        min_value=date_min, max_value=date_max)

    categories = sorted(df["Category"].dropna().unique().tolist())
    selected_categories = st.sidebar.multiselect("Category", options=categories, default=categories)

    exclude_internals = st.sidebar.toggle("Exclude Internal Transfers", value=True)

    # Apply filters
    fdf = df.copy()
    if selected_accounts:
        fdf = fdf[fdf["AccountOrCardLast4"].isin(selected_accounts)]
    if len(date_range) == 2:
        fdf = fdf[(fdf["Date"] >= pd.Timestamp(date_range[0])) &
                  (fdf["Date"] <= pd.Timestamp(date_range[1]))]
    if selected_categories:
        fdf = fdf[fdf["Category"].isin(selected_categories)]
    if exclude_internals:
        fdf = fdf[fdf["Category"] != "Internal Transfer"]

    fdf_spend = fdf[~fdf["IsReversal"]]

    # KPIs
    total_spend  = fdf_spend[fdf_spend["SignedAmount"] < 0]["SignedAmount"].sum()
    total_income = fdf_spend[fdf_spend["SignedAmount"] > 0]["SignedAmount"].sum()
    net_flow     = fdf_spend["SignedAmount"].sum()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Spend",    f"₹{abs(total_spend):,.2f}")
    k2.metric("Total Income",   f"₹{total_income:,.2f}")
    k3.metric("Net Flow",       f"₹{net_flow:,.2f}")
    k4.metric("Transactions",   len(fdf))

    st.divider()

    chart1, chart2, chart3, chart4 = st.tabs(["Category Breakdown", "Monthly Trends", "Top Merchants", "Details"])

    with chart1:
        outflow = fdf_spend[fdf_spend["SignedAmount"] < 0]
        cat_spend = outflow.groupby("Category")["SignedAmount"].sum().abs().sort_values(ascending=False)
        c1, c2 = st.columns(2)
        with c1:
            fig = px.pie(values=cat_spend.values, names=cat_spend.index, title="Spend by Category")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            fig = px.bar(x=cat_spend.head(10).values, y=cat_spend.head(10).index,
                         orientation="h", title="Top 10 Categories (₹)")
            st.plotly_chart(fig, use_container_width=True)

    with chart2:
        fdf_spend = fdf_spend.copy()
        fdf_spend["Month"] = fdf_spend["Date"].dt.to_period("M").astype(str)
        monthly = fdf_spend[fdf_spend["SignedAmount"] < 0].groupby("Month")["SignedAmount"].sum().abs()
        fig = px.line(x=monthly.index, y=monthly.values, markers=True,
                      title="Monthly Spend Trend", labels={"x": "Month", "y": "₹"})
        st.plotly_chart(fig, use_container_width=True)

    with chart3:
        top_m = (fdf_spend[fdf_spend["SignedAmount"] < 0]
                 .groupby("Merchant")["SignedAmount"].sum().abs()
                 .sort_values(ascending=False).head(15))
        fig = px.bar(x=top_m.values, y=top_m.index, orientation="h", title="Top 15 Merchants (₹)")
        st.plotly_chart(fig, use_container_width=True)

    with chart4:
        cols = ["Date", "AccountOrCardLast4", "DescriptionNormalized", "PaymentMode",
                "CounterpartyGuess", "TxnNote", "Category", "Subcategory", "SignedAmount", "NeedsReview"]
        detail = fdf[[c for c in cols if c in fdf.columns]].sort_values("Date", ascending=False).copy()
        detail["SignedAmount"] = detail["SignedAmount"].apply(lambda x: f"₹{x:,.2f}")
        st.dataframe(detail, use_container_width=True, height=500)

    st.divider()
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
