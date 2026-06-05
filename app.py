import sqlite3
import json
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

DB_PATH = "data/ajds.sqlite"

st.set_page_config(
    page_title="Galveston District AJD Database",
    layout="wide"
)

st.title("USACE Galveston District AJD Database")
st.caption("Approved Jurisdictional Determinations extracted from USACE Galveston District 2026 Basis Forms.")


@st.cache_data
def load_data():
    conn = sqlite3.connect(DB_PATH)
    raw_df = pd.read_sql_query("SELECT * FROM ajds ORDER BY swg_number DESC", conn)
    conn.close()

    records = []
    feature_records = []

    for _, row in raw_df.iterrows():
        try:
            data = json.loads(row["extracted_json"])
        except Exception:
            data = {}

        features = data.get("features", [])
        if not isinstance(features, list):
            features = []

        feature_types = sorted(set([
            str(f.get("feature_type", "")).strip()
            for f in features
            if isinstance(f, dict) and f.get("feature_type")
        ]))

        statuses = sorted(set([
            str(f.get("jurisdictional_status", "")).strip()
            for f in features
            if isinstance(f, dict) and f.get("jurisdictional_status")
        ]))

        jd_id = row["swg_number"]

        try:
            year = jd_id.split("-")[1]
        except Exception:
            year = ""

        combined_text = " ".join([
            str(row.get("feature_summary", "")),
            str(row.get("jurisdictional_reasoning", "")),
            str(row.get("jurisdictional_waters", "")),
            str(row.get("non_jurisdictional_features", ""))
        ]).lower()

        if "no waters" in combined_text or "dry land" in combined_text:
            fallback_outcome = "No Waters Present"
            waters_present = "No"
        elif "non-jurisdictional" in combined_text and "jurisdictional" not in str(row.get("jurisdictional_waters", "")).lower():
            fallback_outcome = "Non-Jurisdictional Features"
            waters_present = "No"
        elif "jurisdictional" in combined_text or "waters of the united states" in combined_text:
            fallback_outcome = "Jurisdictional Waters Present"
            waters_present = "Yes"
        else:
            fallback_outcome = "Unclear"
            waters_present = "Unclear"

        determination_outcome = row.get("determination_outcome", "") or fallback_outcome

        if "pre-2015" in combined_text:
            fallback_framework = "Pre-2015 Regulatory Regime"
        elif "sackett" in combined_text:
            fallback_framework = "Post-Sackett"
        else:
            fallback_framework = ""

        regulatory_framework = row.get("regulatory_framework", "") or fallback_framework

        if "relatively permanent" in combined_text or "rpw" in combined_text:
            primary_basis = "Relatively Permanent Water / RPW"
        elif "tnw" in combined_text or "traditional navigable water" in combined_text:
            primary_basis = "Traditional Navigable Water / TNW"
        elif "adjacent" in combined_text:
            primary_basis = "Adjacent Wetland"
        elif "dry land" in combined_text or "no waters" in combined_text:
            primary_basis = "No Aquatic Resources / Dry Land"
        elif "ditch" in combined_text:
            primary_basis = "Ditch Analysis"
        else:
            primary_basis = ""

        records.append({
            "JD ID": jd_id,
            "Project Name": row.get("project_name", ""),
            "County": row.get("county", ""),
            "State": row.get("state", ""),
            "Year": year,
            "JD Date": row.get("issue_date", ""),
            "JD Type": row.get("jd_type", ""),
            "USACE District": row.get("district", "") or "Galveston",
            "Prepared By": row.get("prepared_by", ""),
            "Approved By": row.get("approved_by", ""),
            "Applicant": row.get("applicant", ""),
            "Consultant": row.get("consultant", ""),
            "Feature Type(s)": ", ".join(feature_types),
            "Feature Status(es)": ", ".join(statuses),
            "Waters Present": waters_present,
            "Determination Outcome": determination_outcome,
            "Primary Determination Basis": primary_basis,
            "Regulatory Framework": regulatory_framework,
            "TNW / Receiving Water": row.get("receiving_water", "") or row.get("nearest_waterbody", ""),
            "AI Summary": row.get("feature_summary", ""),
            "Jurisdictional Reasoning": row.get("jurisdictional_reasoning", ""),
            "PDF Link": row.get("pdf_url", ""),
            "Latitude": row.get("latitude", ""),
            "Longitude": row.get("longitude", ""),
            "Full OCR Text": row.get("full_ocr_text", ""),
            "Full Record": row.to_dict()
        })

        for f in features:
            if not isinstance(f, dict):
                continue

            feature_records.append({
                "JD ID": jd_id,
                "County": row.get("county", ""),
                "State": row.get("state", ""),
                "Feature ID": f.get("feature_id", ""),
                "Feature Type": f.get("feature_type", ""),
                "Jurisdictional Status": f.get("jurisdictional_status", ""),
                "Basis": f.get("basis", ""),
                "Reasoning": f.get("reasoning", ""),
                "PDF Link": row.get("pdf_url", "")
            })

    return pd.DataFrame(records), pd.DataFrame(feature_records)


df, features_df = load_data()

st.sidebar.header("Filters")

keyword = st.sidebar.text_input("Keyword Search")

county_filter = st.sidebar.multiselect("County / Parish", sorted(df["County"].dropna().unique()))
state_filter = st.sidebar.multiselect("State", sorted(df["State"].dropna().unique()))
year_filter = st.sidebar.multiselect("Year", sorted(df["Year"].dropna().unique()))

jd_type_filter = st.sidebar.multiselect("JD Type", sorted([x for x in df["JD Type"].dropna().unique() if x]))
prepared_by_filter = st.sidebar.multiselect("Prepared By", sorted([x for x in df["Prepared By"].dropna().unique() if x]))
approved_by_filter = st.sidebar.multiselect("Approved By", sorted([x for x in df["Approved By"].dropna().unique() if x]))
consultant_filter = st.sidebar.multiselect("Consultant", sorted([x for x in df["Consultant"].dropna().unique() if x]))

outcome_filter = st.sidebar.multiselect("Determination Outcome", sorted(df["Determination Outcome"].dropna().unique()))
waters_filter = st.sidebar.multiselect("Waters Present", sorted(df["Waters Present"].dropna().unique()))
basis_filter = st.sidebar.multiselect("Primary Determination Basis", sorted([x for x in df["Primary Determination Basis"].dropna().unique() if x]))
framework_filter = st.sidebar.multiselect("Regulatory Framework", sorted([x for x in df["Regulatory Framework"].dropna().unique() if x]))

feature_options = sorted(set(
    feature.strip()
    for value in df["Feature Type(s)"]
    for feature in str(value).split(",")
    if feature.strip()
))
feature_filter = st.sidebar.multiselect("Feature Type", feature_options)

status_options = sorted(set(
    status.strip()
    for value in df["Feature Status(es)"]
    for status in str(value).split(",")
    if status.strip()
))
status_filter = st.sidebar.multiselect("Feature Jurisdictional Status", status_options)

applicant_contains = st.sidebar.text_input("Applicant Contains")
receiving_water_contains = st.sidebar.text_input("TNW / Receiving Water Contains")

filtered = df.copy()

if keyword:
    filtered = filtered[filtered.apply(lambda row: keyword.lower() in str(row).lower(), axis=1)]

if county_filter:
    filtered = filtered[filtered["County"].isin(county_filter)]

if state_filter:
    filtered = filtered[filtered["State"].isin(state_filter)]

if year_filter:
    filtered = filtered[filtered["Year"].isin(year_filter)]

if jd_type_filter:
    filtered = filtered[filtered["JD Type"].isin(jd_type_filter)]

if prepared_by_filter:
    filtered = filtered[filtered["Prepared By"].isin(prepared_by_filter)]

if approved_by_filter:
    filtered = filtered[filtered["Approved By"].isin(approved_by_filter)]

if consultant_filter:
    filtered = filtered[filtered["Consultant"].isin(consultant_filter)]

if outcome_filter:
    filtered = filtered[filtered["Determination Outcome"].isin(outcome_filter)]

if waters_filter:
    filtered = filtered[filtered["Waters Present"].isin(waters_filter)]

if basis_filter:
    filtered = filtered[filtered["Primary Determination Basis"].isin(basis_filter)]

if framework_filter:
    filtered = filtered[filtered["Regulatory Framework"].isin(framework_filter)]

if feature_filter:
    filtered = filtered[
        filtered["Feature Type(s)"].apply(lambda x: any(feature in str(x) for feature in feature_filter))
    ]

if status_filter:
    filtered = filtered[
        filtered["Feature Status(es)"].apply(lambda x: any(status in str(x) for status in status_filter))
    ]

if applicant_contains:
    filtered = filtered[
        filtered["Applicant"].astype(str).str.lower().str.contains(applicant_contains.lower(), na=False)
    ]

if receiving_water_contains:
    filtered = filtered[
        filtered["TNW / Receiving Water"].astype(str).str.lower().str.contains(receiving_water_contains.lower(), na=False)
    ]

visible_features = features_df[
    features_df["JD ID"].isin(filtered["JD ID"])
] if not features_df.empty else pd.DataFrame()

st.subheader("Summary")

c1, c2, c3, c4, c5, c6 = st.columns(6)

c1.metric("AJDs", len(filtered))
c2.metric("Counties / Parishes", filtered["County"].nunique())
c3.metric("States", filtered["State"].nunique())
c4.metric("Extracted Features", len(visible_features))
c5.metric("Waters Present AJDs", len(filtered[filtered["Waters Present"] == "Yes"]))
c6.metric("Consultants", filtered["Consultant"].replace("", pd.NA).dropna().nunique())

st.divider()

st.subheader("Analytics")

a1, a2 = st.columns(2)

with a1:
    st.write("**AJDs by County / Parish**")
    county_counts = filtered["County"].value_counts().reset_index()
    county_counts.columns = ["County / Parish", "Count"]
    st.bar_chart(county_counts.set_index("County / Parish"))

with a2:
    st.write("**Determination Outcomes**")
    outcome_counts = filtered["Determination Outcome"].value_counts().reset_index()
    outcome_counts.columns = ["Outcome", "Count"]
    st.bar_chart(outcome_counts.set_index("Outcome"))

a3, a4, a5 = st.columns(3)

with a3:
    st.write("**Top Consultants**")
    consultant_counts = filtered["Consultant"].replace("", pd.NA).dropna().value_counts().head(10).reset_index()
    consultant_counts.columns = ["Consultant", "Count"]
    if not consultant_counts.empty:
        st.bar_chart(consultant_counts.set_index("Consultant"))
    else:
        st.info("No consultant data available.")

with a4:
    st.write("**Top Preparers**")
    preparer_counts = filtered["Prepared By"].replace("", pd.NA).dropna().value_counts().head(10).reset_index()
    preparer_counts.columns = ["Prepared By", "Count"]
    if not preparer_counts.empty:
        st.bar_chart(preparer_counts.set_index("Prepared By"))
    else:
        st.info("No preparer data available.")

with a5:
    st.write("**Top Approvers**")
    approver_counts = filtered["Approved By"].replace("", pd.NA).dropna().value_counts().head(10).reset_index()
    approver_counts.columns = ["Approved By", "Count"]
    if not approver_counts.empty:
        st.bar_chart(approver_counts.set_index("Approved By"))
    else:
        st.info("No approver data available.")

st.divider()

st.subheader("Clickable AJD Map")

map_df = filtered.copy()
map_df["lat"] = pd.to_numeric(map_df["Latitude"], errors="coerce")
map_df["lon"] = pd.to_numeric(map_df["Longitude"], errors="coerce")
map_df = map_df.dropna(subset=["lat", "lon"])

if not map_df.empty:
    center_lat = map_df["lat"].mean()
    center_lon = map_df["lon"].mean()

    m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles="OpenStreetMap")

    for _, row in map_df.iterrows():
        popup_html = f"""
        <b>{row['JD ID']}</b><br>
        <b>County:</b> {row['County']}<br>
        <b>State:</b> {row['State']}<br>
        <b>Outcome:</b> {row['Determination Outcome']}<br>
        <b>Prepared By:</b> {row['Prepared By']}<br>
        <b>Approved By:</b> {row['Approved By']}<br>
        <b>Consultant:</b> {row['Consultant']}<br>
        <b>Feature Types:</b> {row['Feature Type(s)']}<br>
        <b>Summary:</b> {row['AI Summary']}<br>
        <a href="{row['PDF Link']}" target="_blank">Open PDF</a>
        """

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=7,
            popup=folium.Popup(popup_html, max_width=500),
            tooltip=row["JD ID"],
            fill=True,
            fill_opacity=0.8
        ).add_to(m)

    st_folium(m, width=1200, height=500)
else:
    st.info("No valid coordinates available for the current filters.")

st.divider()

st.subheader("AJD Table")

table_columns = [
    "JD ID",
    "Project Name",
    "County",
    "State",
    "Year",
    "JD Date",
    "JD Type",
    "USACE District",
    "Prepared By",
    "Approved By",
    "Applicant",
    "Consultant",
    "Feature Type(s)",
    "Feature Status(es)",
    "Waters Present",
    "Determination Outcome",
    "Primary Determination Basis",
    "Regulatory Framework",
    "TNW / Receiving Water",
    "PDF Link"
]

st.dataframe(
    filtered[table_columns],
    use_container_width=True,
    hide_index=True,
    column_config={
        "PDF Link": st.column_config.LinkColumn("PDF Link")
    }
)

st.divider()

st.subheader("Extracted Feature Table")

if not visible_features.empty:
    st.dataframe(
        visible_features,
        use_container_width=True,
        hide_index=True,
        column_config={
            "PDF Link": st.column_config.LinkColumn("PDF Link")
        }
    )
else:
    st.info("No extracted features found for the current filters.")

st.divider()

st.subheader("Select AJD Detail Card")

if filtered.empty:
    st.warning("No records match the selected filters.")
else:
    selected_jd = st.selectbox(
        "Select a JD ID to view details",
        filtered["JD ID"].tolist()
    )

    selected_row = filtered[filtered["JD ID"] == selected_jd].iloc[0]

    st.write("### Basic Information")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.write("**JD ID:**", selected_row["JD ID"])
        st.write("**Project Name:**", selected_row["Project Name"])
        st.write("**County:**", selected_row["County"])
        st.write("**State:**", selected_row["State"])
        st.write("**Year:**", selected_row["Year"])
        st.write("**JD Date:**", selected_row["JD Date"])

    with c2:
        st.write("**JD Type:**", selected_row["JD Type"])
        st.write("**USACE District:**", selected_row["USACE District"])
        st.write("**Prepared By:**", selected_row["Prepared By"])
        st.write("**Approved By:**", selected_row["Approved By"])
        st.write("**Applicant:**", selected_row["Applicant"])
        st.write("**Consultant:**", selected_row["Consultant"])

    with c3:
        st.write("**Waters Present:**", selected_row["Waters Present"])
        st.write("**Determination Outcome:**", selected_row["Determination Outcome"])
        st.write("**Primary Basis:**", selected_row["Primary Determination Basis"])
        st.write("**Regulatory Framework:**", selected_row["Regulatory Framework"])
        st.write("**TNW / Receiving Water:**", selected_row["TNW / Receiving Water"])
        st.write("**Feature Type(s):**", selected_row["Feature Type(s)"])

    st.write("### AI Summary")
    st.write(selected_row["AI Summary"])

    st.write("### Jurisdictional Reasoning")
    st.write(selected_row["Jurisdictional Reasoning"])

    selected_features = visible_features[visible_features["JD ID"] == selected_jd]

    if not selected_features.empty:
        st.write("### Extracted Features")
        st.dataframe(
            selected_features,
            use_container_width=True,
            hide_index=True,
            column_config={
                "PDF Link": st.column_config.LinkColumn("PDF Link")
            }
        )

    if selected_row["PDF Link"]:
        st.link_button("Open Original PDF", selected_row["PDF Link"])

    with st.expander("Full OCR Text"):
        st.write(selected_row["Full OCR Text"])

    with st.expander("Full Database Record"):
        st.json(selected_row["Full Record"])