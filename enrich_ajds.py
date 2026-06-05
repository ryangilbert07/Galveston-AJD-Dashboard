import json
import sqlite3
from openai import OpenAI

DB_PATH = "data/ajds.sqlite"
AI_MODEL = "gpt-4o-mini"

client = OpenAI()


def setup_new_columns():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    new_columns = {
        "jd_type": "TEXT",
        "prepared_by": "TEXT",
        "approved_by": "TEXT",
        "consultant": "TEXT",
        "district": "TEXT",
        "determination_outcome": "TEXT",
        "regulatory_framework": "TEXT",
        "receiving_water": "TEXT"
    }

    for column, col_type in new_columns.items():
        try:
            cur.execute(f"ALTER TABLE ajds ADD COLUMN {column} {col_type}")
            print(f"Added column: {column}")
        except sqlite3.OperationalError:
            print(f"Column already exists: {column}")

    conn.commit()
    conn.close()


def extract_extra_fields(swg_number, full_text):
    prompt = f"""
You are extracting structured data from a USACE Galveston District Approved Jurisdictional Determination.

Return ONLY valid JSON. Do not include markdown.

Use null if the field is not found.

Extract these fields:

{{
  "jd_type": "",
  "prepared_by": "",
  "approved_by": "",
  "consultant": "",
  "district": "",
  "determination_outcome": "",
  "regulatory_framework": "",
  "receiving_water": ""
}}

Definitions:
- jd_type: examples include Approved Jurisdictional Determination, AJD, MFR, Basis Form, No Permit Required, etc.
- prepared_by: person who prepared the document, if listed.
- approved_by: person who signed or approved the determination, if listed.
- consultant: private consultant or agent, if listed.
- district: USACE district, usually Galveston District.
- determination_outcome: short plain-English outcome, such as No Waters Present, Jurisdictional Waters Present, Non-Jurisdictional Features, No Section 404 Waters, Section 10 Waters Present, etc.
- regulatory_framework: examples include Pre-2015 Regulatory Regime, Post-Sackett, 2023 Rule, Rapanos, Relatively Permanent Standard, etc.
- receiving_water: nearest TNW, RPW, traditional navigable water, receiving water, bay, river, canal, channel, or named waterbody.

SWG Number:
{swg_number}

OCR TEXT:
{full_text[:50000]}
"""

    response = client.responses.create(
        model=AI_MODEL,
        input=prompt
    )

    raw = response.output_text.strip()

    try:
        return json.loads(raw)
    except Exception:
        print(f"Could not parse JSON for {swg_number}")
        return {}


def update_record(swg_number, extracted):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE ajds
        SET
            jd_type = ?,
            prepared_by = ?,
            approved_by = ?,
            consultant = ?,
            district = ?,
            determination_outcome = ?,
            regulatory_framework = ?,
            receiving_water = ?
        WHERE swg_number = ?
        """,
        (
            extracted.get("jd_type") or "",
            extracted.get("prepared_by") or "",
            extracted.get("approved_by") or "",
            extracted.get("consultant") or "",
            extracted.get("district") or "",
            extracted.get("determination_outcome") or "",
            extracted.get("regulatory_framework") or "",
            extracted.get("receiving_water") or "",
            swg_number
        )
    )

    conn.commit()
    conn.close()


def main():
    setup_new_columns()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT swg_number, full_ocr_text
        FROM ajds
        WHERE full_ocr_text IS NOT NULL
        """
    )

    rows = cur.fetchall()
    conn.close()

    print(f"Found {len(rows)} records to enrich.")

    for swg_number, full_text in rows:
        print(f"Enriching {swg_number}")

        extracted = extract_extra_fields(swg_number, full_text)
        update_record(swg_number, extracted)

    print("Done enriching AJD records.")


if __name__ == "__main__":
    main()