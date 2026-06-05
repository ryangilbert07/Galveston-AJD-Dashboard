import sqlite3
import json
import os
from io import BytesIO
from datetime import datetime

import pandas as pd
import streamlit as st
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

DB_PATH = "data/ajds.sqlite"

st.set_page_config(
    page_title="USACE Galveston District AJD Database",
    layout="wide"
)

st.title("USACE Galveston District AJD Database")

st.warning(
    "Unofficial research tool. Data were extracted from publicly available USACE AJD PDFs using OCR and AI-assisted parsing. "
    "Verify all information against the original PDF before relying on it for regulatory, legal, or permitting decisions."
)


def normalize_text(value):
    if value is None:
        return ""
    value = str(value).strip()
    if value.lower() in ["none", "null", "nan", "n/a", "na"]:
        return ""
    return value


def normalize_outcome(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return "Unknown"

    if "no waters" in text or "dry land" in text:
        return "No Waters Present"

    if "non-jurisdictional" in text or "nonjurisdictional" in text:
        return "Non-Jurisdictional Features"

    if "jurisdictional waters present" in text:
        return "Jurisdictional Waters Present"

    if "jurisdictional" in text and "non" not in text:
        return "Jurisdictional Waters Present"

    return value


def normalize_consultant(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return ""

    if "marcia appia" in text:
        return "Marcia Appia Engineers, LLC"

    if "raba kistner" in text:
        return "Raba Kistner"

    if "swca" in text:
        return "SWCA Environmental Consultants"

    if "freese" in text and "nichols" in text:
        return "Freese and Nichols"

    if "power engineers" in text:
        return "Power Engineers"

    if "kimley" in text and "horn" in text:
        return "Kimley-Horn"

    if "dewberry" in text:
        return "Dewberry Engineers, Inc."

    if "benchmark" in text:
        return "Benchmark Ecological Services Inc."

    if "harris county flood control" in text:
        return "Harris County Flood Control District"

    return value


def normalize_approved_by(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return ""

    if "andria" in text and "davis" in text:
        return "Andria Davis"

    if "kara" in text and "vick" in text:
        return "Kara Vick Clark"

    if "karie" in text and "vick" in text:
        return "Kara Vick Clark"

    if "marie" in text and "taylor" in text:
        return "K. Marie Taylor"

    if "matthew" in text and "kimmel" in text:
        return "Matthew Kimmel"

    if "matther" in text and "kimmel" in text:
        return "Matthew Kimmel"

    return value


def normalize_prepared_by(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return ""

    if "bogrand" in text:
        return "Ashley Bogard"

    if "avettin" in text or "avertin" in text:
        return "Avettin Wore"

    if "wiki" in text:
        return "Avettin Wore"

    return value


@st.cache_data
def load_data():
    conn = sqlite3.connect(DB_PATH)
    raw_df = pd.read_sql_query("SELECT * FROM ajds ORDER BY swg_number DESC", conn)
    conn.close()

    records = []
    feature_records = []

    for _, row in raw_df.iterrows():
        try:
            data = json.loads(row.get("extracted_json", "{}"))
        except Exception:
            data = {}

        features = data.get("features", [])
        if not isinstance(features, list):
            features = []

        feature_types = sorted(set([
            normalize_text(f.get("feature_type", ""))
            for f in features
            if isinstance(f, dict) and normalize_text(f.get("feature_type", ""))
        ]))

        statuses = sorted(set([
            normalize_outcome(f.get("jurisdictional_status", ""))
            for f in features
            if isinstance(f, dict) and normalize_text(f.get("jurisdictional_status", ""))
        ]))

        jd_id = normalize_text(row.get("swg_number", ""))

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

        fallback_outcome = "Unknown"
        waters_present = "Unclear"

        if "no waters" in combined_text or "dry land" in combined_text:
            fallback_outcome = "No Waters Present"
            waters_present = "No"
        elif "non-jurisdictional" in combined_text and "jurisdictional" not in str(row.get("jurisdictional_waters", "")).lower():
            fallback_outcome = "Non-Jurisdictional Features"
            waters_present = "No"
        elif "jurisdictional" in combined_text or "waters of the united states" in combined_text:
            fallback_outcome = "Jurisdictional Waters Present"
            waters_present = "Yes"

        determination_outcome = normalize_outcome(row.get("determination_outcome", "")) or fallback_outcome
        if determination_outcome == "Unknown":
            determination_outcome = fallback_outcome

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

        consultant = normalize_consultant(row.get("consultant", ""))
        prepared_by = normalize_prepared_by(row.get("prepared_by", ""))
        approved_by = normalize_approved_by(row.get("approved_by", ""))

        quality_issues = []

        if not row.get("latitude") or not row.get("longitude"):
            quality_issues.append("Missing Coordinates")

        if not feature_types:
            quality_issues.append("No Features Extracted")

        if not row.get("project_name"):
            quality_issues.append("Missing Project Name")

        if not prepared_by:
            quality_issues.append("Missing Prepared By")

        if not approved_by:
            quality_issues.append("Missing Approved By")

        quality_flag = "Good" if not quality_issues else "; ".join(quality_issues)

        records.append({
            "JD ID": jd_id,
            "Project Name": normalize_text(row.get("project_name", "")),
            "County": normalize_text(row.get("county", "")),
            "State": normalize_text(row.get("state", "")),
            "Year": year,
            "JD Date": normalize_text(row.get("issue_date", "")),
            "JD Type": normalize_text(row.get("jd_type", "")),
            "USACE District": normalize_text(row.get("district", "")) or "Galveston",
            "Prepared By": prepared_by,
            "Approved By": approved_by,
            "Applicant": normalize_text(row.get("applicant", "")),
            "Consultant": consultant,
            "Feature Type(s)": ", ".join(feature_types),
            "Feature Status(es)": ", ".join(statuses),
            "Waters Present": waters_present,
            "Determination Outcome": determination_outcome,
            "Primary Determination Basis": primary_basis,
            "TNW / Receiving Water": normalize_text(row.get("receiving_water", "")) or normalize_text(row.get("nearest_waterbody", "")),
            "AI Summary": normalize_text(row.get("feature_summary", "")),
            "Jurisdictional Reasoning": normalize_text(row.get("jurisdictional_reasoning", "")),
            "PDF Link": normalize_text(row.get("pdf_url", "")),
            "Latitude": normalize_text(row.get("latitude", "")),
            "Longitude": normalize_text(row.get("longitude", "")),
            "Quality Flag": quality_flag,
            "Full OCR Text": normalize_text(row.get("full_ocr_text", "")),
            "Full Record": row.to_dict()
        })

        for f in features:
            if not isinstance(f, dict):
                continue

            feature_status = normalize_outcome(f.get("jurisdictional_status", ""))

            feature_records.append({
                "JD ID": jd_id,
                "County": normalize_text(row.get("county", "")),
                "State": normalize_text(row.get("state", "")),
                "Feature ID": normalize_text(f.get("feature_id", "")),
                "Feature Type": normalize_text(f.get("feature_type", "")),
                "Jurisdictional Status": feature_status,
                "Basis": normalize_text(f.get("basis", "")),
                "Reasoning": normalize_text(f.get("reasoning", "")),
                "PDF Link": normalize_text(row.get("pdf_url", ""))
            })

    return pd.DataFrame(records), pd.DataFrame(feature_records)


def make_excel_download(ajd_df, feature_df):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        ajd_df.to_excel(writer, index=False, sheet_name="AJDs")
        feature_df.to_excel(writer, index=False, sheet_name="Features")

    return output.getvalue()


def marker_color(outcome):
    text = str(outcome).lower()

    if "jurisdictional waters present" in text:
        return "green"
    if "non-jurisdictional" in text:
        return "red"
    if "no waters" in text:
        return "gray"

    return "blue"


df, features_df = load_data()

if os.path.exists(DB_PATH):
    db_modified = datetime.fromtimestamp(os.path.getmtime(DB_PATH))
    st.caption(f"Database last updated: {db_modified.strftime('%Y-%m-%d %H:%M')}")

st.sidebar.header("Filters")

keyword = st.sidebar.text_input("Keyword Search")

county_filter = st.sidebar.multiselect("County / Parish", sorted(df["County"].dropna().unique()))
state_filter = st.sidebar.multiselect("State", sorted(df["State"].dropna().unique()))
year_filter = st.sidebar.multiselect("Year", sorted(df["Year"].dropna().unique()))

outcome_filter = st.sidebar.multiselect("Determination Outcome", sorted(df["Determination Outcome"].dropna().unique()))

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

consultant_filter = st.sidebar.multiselect(
    "Consultant",
    sorted([x for x in df["Consultant"].dropna().unique() if x])
)

quality_filter = st.sidebar.multiselect("Quality Flag", sorted(df["Quality Flag"].dropna().unique()))

with st.sidebar.expander("Advanced Filters"):
    prepared_by_filter = st.multiselect(
        "Prepared By",
        sorted([x for x in df["Prepared By"].dropna().unique() if x])
    )

    approved_by_filter = st.multiselect(
        "Approved By",
        sorted([x for x in df["Approved By"].dropna().unique() if x])
    )

    waters_filter = st.multiselect(
        "Waters Present",
        sorted(df["Waters Present"].dropna().unique())
    )

    basis_filter = st.multiselect(
        "Primary Determination Basis",
        sorted([x for x in df["Primary Determination Basis"].dropna().unique() if x])
    )

    applicant_contains = st.text_input("Applicant Contains")
    receiving_water_contains = st.text_input("TNW / Receiving Water Contains")

filtered = df.copy()

if keyword:
    filtered = filtered[filtered.apply(lambda row: keyword.lower() in str(row).lower(), axis=1)]

if county_filter:
    filtered = filtered[filtered["County"].isin(county_filter)]

if state_filter:
    filtered = filtered[filtered["State"].isin(state_filter)]

if year_filter:
    filtered = filtered[filtered["Year"].isin(year_filter)]

if outcome_filter:
    filtered = filtered[filtered["Determination Outcome"].isin(outcome_filter)]

if feature_filter:
    filtered = filtered[
        filtered["Feature Type(s)"].apply(lambda x: any(feature in str(x) for feature in feature_filter))
    ]

if status_filter:
    filtered = filtered[
        filtered["Feature Status(es)"].apply(lambda x: any(status in str(x) for status in status_filter))
    ]

if consultant_filter:
    filtered = filtered[filtered["Consultant"].isin(consultant_filter)]

if quality_filter:
    filtered = filtered[filtered["Quality Flag"].isin(quality_filter)]

if prepared_by_filter:
    filtered = filtered[filtered["Prepared By"].isin(prepared_by_filter)]

if approved_by_filter:
    filtered = filtered[filtered["Approved By"].isin(approved_by_filter)]

if waters_filter:
    filtered = filtered[filtered["Waters Present"].isin(waters_filter)]

if basis_filter:
    filtered = filtered[filtered["Primary Determination Basis"].isin(basis_filter)]

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

st.subheader("Summary Metrics")

c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns(9)

c1.metric("AJDs", len(filtered))
c2.metric("Counties", filtered["County"].nunique())
c3.metric("States", filtered["State"].nunique())
c4.metric("Features", len(visible_features))
c5.metric("Waters Present", len(filtered[filtered["Waters Present"] == "Yes"]))
c6.metric("Consultants", filtered["Consultant"].replace("", pd.NA).dropna().nunique())
c7.metric("Needs Review", len(filtered[filtered["Quality Flag"] != "Good"]))
c8.metric("With Coordinates", len(filtered[(filtered["Latitude"] != "") & (filtered["Longitude"] != "")]))
c9.metric("With Consultant", len(filtered[filtered["Consultant"] != ""]))

st.divider()

st.subheader("AJD Map")

map_df = filtered.copy()
map_df["lat"] = pd.to_numeric(map_df["Latitude"], errors="coerce")
map_df["lon"] = pd.to_numeric(map_df["Longitude"], errors="coerce")
map_df = map_df.dropna(subset=["lat", "lon"])

if not map_df.empty:
    center_lat = map_df["lat"].mean()
    center_lon = map_df["lon"].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=7,
        control_scale=True,
        tiles=None
    )

    folium.TileLayer(
        "OpenStreetMap",
        name="Street Map"
    ).add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Aerial Imagery"
    ).add_to(m)

    folium.TileLayer(
        tiles="https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Aerial Labels",
        overlay=True,
        control=True
    ).add_to(m)

    marker_cluster = MarkerCluster(name="AJD Locations").add_to(m)

    for _, row in map_df.iterrows():
        color = marker_color(row["Determination Outcome"])

        popup_html = f"""
        <b>{row['JD ID']}</b><br>
        <b>County:</b> {row['County']}<br>
        <b>State:</b> {row['State']}<br>
        <b>Outcome:</b> {row['Determination Outcome']}<br>
        <b>Prepared By:</b> {row['Prepared By']}<br>
        <b>Approved By:</b> {row['Approved By']}<br>
        <b>Consultant:</b> {row['Consultant']}<br>
        <b>Feature Types:</b> {row['Feature Type(s)']}<br>
        <b>Quality:</b> {row['Quality Flag']}<br>
        <b>Summary:</b> {row['AI Summary']}<br>
        <a href="{row['PDF Link']}" target="_blank">Open PDF</a>
        """

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=7,
            popup=folium.Popup(popup_html, max_width=500),
            tooltip=row["JD ID"],
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8
        ).add_to(marker_cluster)

    folium.LayerControl().add_to(m)

    st_folium(m, width=1400, height=650)
else:
    st.info("No valid coordinates available for the current filters.")

st.divider()

tabs = st.tabs([
    "Overview",
    "AJD Table",
    "Feature Explorer",
    "Detail View",
    "Downloads",
    "About / Methodology"
])

with tabs[0]:
    st.subheader("Analytics")

    a1, a2 = st.columns(2)

    with a1:
        st.write("### AJDs by County / Parish")
        county_counts = filtered["County"].value_counts().reset_index()
        county_counts.columns = ["County / Parish", "Count"]
        if not county_counts.empty:
            st.bar_chart(county_counts.set_index("County / Parish"))

    with a2:
        st.write("### Determination Outcomes")
        outcome_counts = filtered["Determination Outcome"].value_counts().reset_index()
        outcome_counts.columns = ["Outcome", "Count"]
        if not outcome_counts.empty:
            st.bar_chart(outcome_counts.set_index("Outcome"))

    a3, a4, a5 = st.columns(3)

    with a3:
        st.write("### Top Consultants")
        consultant_counts = filtered["Consultant"].replace("", pd.NA).dropna().value_counts().head(10).reset_index()
        consultant_counts.columns = ["Consultant", "Count"]
        if not consultant_counts.empty:
            st.bar_chart(consultant_counts.set_index("Consultant"))

    with a4:
        st.write("### Top Preparers")
        preparer_counts = filtered["Prepared By"].replace("", pd.NA).dropna().value_counts().head(10).reset_index()
        preparer_counts.columns = ["Prepared By", "Count"]
        if not preparer_counts.empty:
            st.bar_chart(preparer_counts.set_index("Prepared By"))

    with a5:
        st.write("### Top Approvers")
        approver_counts = filtered["Approved By"].replace("", pd.NA).dropna().value_counts().head(10).reset_index()
        approver_counts.columns = ["Approved By", "Count"]
        if not approver_counts.empty:
            st.bar_chart(approver_counts.set_index("Approved By"))

    st.write("### Feature Type Frequency")
    if not visible_features.empty:
        feature_counts = visible_features["Feature Type"].replace("", pd.NA).dropna().value_counts().head(20)
        st.bar_chart(feature_counts)
    else:
        st.info("No feature data available.")

with tabs[1]:
    st.subheader("AJD Table")

    table_columns = [
        "JD ID",
        "Project Name",
        "County",
        "State",
        "Year",
        "JD Date",
        "Prepared By",
        "Approved By",
        "Applicant",
        "Consultant",
        "Feature Type(s)",
        "Feature Status(es)",
        "Waters Present",
        "Determination Outcome",
        "Primary Determination Basis",
        "TNW / Receiving Water",
        "Quality Flag",
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

with tabs[2]:
    st.subheader("Feature Explorer")

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

with tabs[3]:
    st.subheader("Detail View")

    if filtered.empty:
        st.warning("No records match the selected filters.")
    else:
        selected_jd = st.selectbox(
            "Select a JD ID to view details",
            filtered["JD ID"].tolist()
        )

        selected_row = filtered[filtered["JD ID"] == selected_jd].iloc[0]

        st.header(selected_row["JD ID"])
        st.caption(f"{selected_row['County']}, {selected_row['State']} | {selected_row['JD Date']}")

        if selected_row["Waters Present"] == "Yes":
            st.success(f"Determination Outcome: {selected_row['Determination Outcome']}")
        elif selected_row["Waters Present"] == "No":
            st.warning(f"Determination Outcome: {selected_row['Determination Outcome']}")
        else:
            st.info(f"Determination Outcome: {selected_row['Determination Outcome']}")

        c1, c2, c3 = st.columns(3)

        with c1:
            st.write("**Project Name:**", selected_row["Project Name"])
            st.write("**Applicant:**", selected_row["Applicant"])
            st.write("**Consultant:**", selected_row["Consultant"])
            st.write("**USACE District:**", selected_row["USACE District"])

        with c2:
            st.write("**Prepared By:**", selected_row["Prepared By"])
            st.write("**Approved By:**", selected_row["Approved By"])
            st.write("**JD Type:**", selected_row["JD Type"])
            st.write("**Quality Flag:**", selected_row["Quality Flag"])

        with c3:
            st.write("**Primary Basis:**", selected_row["Primary Determination Basis"])
            st.write("**TNW / Receiving Water:**", selected_row["TNW / Receiving Water"])
            st.write("**Feature Type(s):**", selected_row["Feature Type(s)"])
            st.write("**Waters Present:**", selected_row["Waters Present"])

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

with tabs[4]:
    st.subheader("Downloads")

    st.write("Download the currently filtered results.")

    csv_ajds = filtered.drop(columns=["Full Record"], errors="ignore").to_csv(index=False).encode("utf-8")

    st.download_button(
        label="Download Filtered AJDs as CSV",
        data=csv_ajds,
        file_name="filtered_ajds.csv",
        mime="text/csv"
    )

    if not visible_features.empty:
        csv_features = visible_features.to_csv(index=False).encode("utf-8")

        st.download_button(
            label="Download Filtered Features as CSV",
            data=csv_features,
            file_name="filtered_features.csv",
            mime="text/csv"
        )

    excel_file = make_excel_download(
        filtered.drop(columns=["Full Record"], errors="ignore"),
        visible_features
    )

    st.download_button(
        label="Download Filtered AJDs and Features as Excel",
        data=excel_file,
        file_name="filtered_ajd_database.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

with tabs[5]:
    st.header("About / Methodology")

    st.markdown(
        """
        ### Data Source

        This dashboard is based on publicly available USACE Galveston District Approved Jurisdictional Determination documents.

        ### Processing Method

        Source PDFs were downloaded, OCR processed, and parsed using AI-assisted extraction. Extracted fields include JD ID, county, state, project name, applicant, consultant, USACE reviewer information, feature types, determination outcome, jurisdictional reasoning, and PDF links.

        ### Limitations

        OCR and AI-assisted extraction may produce errors or omissions. The dashboard should be used as a research aid, not as the official jurisdictional determination record.

        ### Recommended Use

        Use this tool for regulatory research, due diligence, trend analysis, and locating similar AJDs. Always open and verify the original PDF before relying on extracted information.

        ### Quality Flags

        Records marked as needing review may have missing coordinates, missing project names, or limited extracted feature information.
        """
    )