import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# Page config
st.set_page_config(page_title="Expense Tracker", layout="wide")
st.title("📊 Personal Finance Dashboard")

# Load data
@st.cache_data
def load_data():
    return pd.read_csv("expenses/data/db/enriched_ledger.csv")

df = load_data()
df["Date"] = pd.to_datetime(df["Date"])

# Sidebar filters
st.sidebar.header("Filters")
date_range = st.sidebar.date_input(
    "Date Range",
    value=(df["Date"].min(), df["Date"].max()),
    min_value=df["Date"].min(),
    max_value=df["Date"].max()
)

selected_category = st.sidebar.multiselect(
    "Category",
    options=["All"] + sorted(df["Category"].unique().tolist()),
    default="All"
)

# Filter data
filtered_df = df[
    (df["Date"] >= pd.Timestamp(date_range[0])) &
    (df["Date"] <= pd.Timestamp(date_range[1]))
]

if "All" not in selected_category and selected_category:
    filtered_df = filtered_df[filtered_df["Category"].isin(selected_category)]

# KPIs
col1, col2, col3, col4 = st.columns(4)

total_spend = filtered_df[filtered_df["SignedAmount"] < 0]["SignedAmount"].sum()
total_income = filtered_df[filtered_df["SignedAmount"] > 0]["SignedAmount"].sum()
net_flow = filtered_df["SignedAmount"].sum()
transaction_count = len(filtered_df)

col1.metric("💰 Total Spend", f"₹{abs(total_spend):,.2f}")
col2.metric("💵 Total Income", f"₹{total_income:,.2f}")
col3.metric("📈 Net Flow", f"₹{net_flow:,.2f}")
col4.metric("📋 Transactions", transaction_count)

st.divider()

# Charts
tab1, tab2, tab3, tab4 = st.tabs(["Category Breakdown", "Monthly Trends", "Top Merchants", "Details"])

with tab1:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Spending by Category")
        category_spend = filtered_df[filtered_df["SignedAmount"] < 0].groupby("Category")["SignedAmount"].sum().abs().sort_values(ascending=False)
        fig_cat = px.pie(
            values=category_spend.values,
            names=category_spend.index,
            title="Distribution"
        )
        st.plotly_chart(fig_cat, use_container_width=True)

    with col2:
        st.subheader("Top 10 Categories (by spend)")
        fig_bar = px.bar(
            x=category_spend.head(10).values,
            y=category_spend.head(10).index,
            orientation="h",
            title="Amount (₹)"
        )
        st.plotly_chart(fig_bar, use_container_width=True)

with tab2:
    st.subheader("Monthly Spending Trend")
    filtered_df["Month"] = filtered_df["Date"].dt.to_period("M").astype(str)
    monthly = filtered_df[filtered_df["SignedAmount"] < 0].groupby("Month")["SignedAmount"].sum().abs()

    fig_trend = px.line(
        x=monthly.index,
        y=monthly.values,
        markers=True,
        title="Monthly Spend Trend",
        labels={"x": "Month", "y": "Spend (₹)"}
    )
    st.plotly_chart(fig_trend, use_container_width=True)

with tab3:
    st.subheader("Top Merchants")
    top_merchants = filtered_df[filtered_df["SignedAmount"] < 0].groupby("Merchant")["SignedAmount"].sum().abs().sort_values(ascending=False).head(15)

    fig_merch = px.bar(
        x=top_merchants.values,
        y=top_merchants.index,
        orientation="h",
        title="Top 15 Merchants by Spend"
    )
    st.plotly_chart(fig_merch, use_container_width=True)

with tab4:
    st.subheader("Transaction Details")

    # Show detailed table
    display_cols = ["Date", "DescriptionNormalized", "Category", "Subcategory", "SignedAmount"]
    detail_df = filtered_df[display_cols].sort_values("Date", ascending=False).copy()
    detail_df["SignedAmount"] = detail_df["SignedAmount"].apply(lambda x: f"₹{x:,.2f}")

    st.dataframe(detail_df, use_container_width=True, height=500)

st.divider()
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
