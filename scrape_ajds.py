import os
import re
import json
import sqlite3
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract
from openai import OpenAI
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://www.swg.usace.army.mil"
JD_PAGE_HTML = "jd_page.html"

DATA_DIR = "data"
PDF_DIR = "pdfs"
DB_PATH = os.path.join(DATA_DIR, "ajds.sqlite")
CHROME_PROFILE_DIR = "chrome_profile_for_downloads"

AI_MODEL = "gpt-4o-mini"

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\poppler\poppler-26.02.0\Library\bin"

client = OpenAI()


def setup_folders():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PDF_DIR, exist_ok=True)
    os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)


def setup_chrome_preferences():
    default_dir = os.path.join(CHROME_PROFILE_DIR, "Default")
    os.makedirs(default_dir, exist_ok=True)

    prefs_path = os.path.join(default_dir, "Preferences")
    download_dir = os.path.abspath(PDF_DIR)

    prefs = {
        "download": {
            "default_directory": download_dir,
            "prompt_for_download": False,
            "directory_upgrade": True
        },
        "plugins": {
            "always_open_pdf_externally": True
        },
        "profile": {
            "default_content_settings": {
                "popups": 0
            }
        }
    }

    with open(prefs_path, "w", encoding="utf-8") as f:
        json.dump(prefs, f)


def setup_database():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ajds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            swg_number TEXT UNIQUE,
            pdf_url TEXT,
            local_pdf_path TEXT,
            issue_date TEXT,
            county TEXT,
            state TEXT,
            latitude TEXT,
            longitude TEXT,
            project_name TEXT,
            applicant TEXT,
            nearest_waterbody TEXT,
            feature_summary TEXT,
            jurisdictional_waters TEXT,
            non_jurisdictional_features TEXT,
            jurisdictional_reasoning TEXT,
            extracted_json TEXT,
            full_ocr_text TEXT,
            date_added TEXT,
            last_updated TEXT
        )
    """)

    conn.commit()
    conn.close()


def already_processed(swg_number):
    if not os.path.exists(DB_PATH):
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ajds WHERE swg_number = ?", (swg_number,))
    count = cur.fetchone()[0]
    conn.close()

    return count > 0


def get_2026_pdf_links():
    if not os.path.exists(JD_PAGE_HTML):
        raise FileNotFoundError(
            "jd_page.html was not found. Save the USACE page as jd_page.html in this folder."
        )

    with open(JD_PAGE_HTML, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find(
        lambda tag: tag.name in ["h2", "h3", "h4"]
        and "2026 Basis Forms" in tag.get_text(strip=True)
    )

    if heading is None:
        raise RuntimeError("Could not find the 2026 Basis Forms heading.")

    links = []

    for element in heading.find_all_next():
        if element.name in ["h2", "h3", "h4"] and "2026 Basis Forms" not in element.get_text(strip=True):
            break

        if element.name == "a":
            href = element.get("href", "")
            text = element.get_text(strip=True)

            if ".pdf" in href.lower():
                pdf_url = urljoin(BASE_URL, href)

                swg_match = re.search(r"SWG[-_ ]?\d{4}[-_ ]?\d+", text, re.IGNORECASE)
                if not swg_match:
                    swg_match = re.search(r"SWG[-_ ]?\d{4}[-_ ]?\d+", pdf_url, re.IGNORECASE)

                if swg_match:
                    swg_number = swg_match.group(0).replace("_", "-").replace(" ", "-")
                else:
                    swg_number = text or os.path.basename(pdf_url)

                links.append((swg_number, pdf_url))

    unique = []
    seen = set()

    for swg_number, pdf_url in links:
        if pdf_url not in seen:
            seen.add(pdf_url)
            unique.append((swg_number, pdf_url))

    return unique


def is_valid_pdf(local_path):
    if not os.path.exists(local_path):
        return False

    try:
        with open(local_path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


def download_pdf_with_chrome(context, swg_number, pdf_url):
    clean_name = re.sub(r"[^A-Za-z0-9_-]", "_", swg_number)
    local_path = os.path.abspath(os.path.join(PDF_DIR, f"{clean_name}.pdf"))

    if os.path.exists(local_path):
        if is_valid_pdf(local_path):
            print(f"PDF already exists: {local_path}")
            return local_path
        else:
            print(f"Deleting invalid PDF file: {local_path}")
            os.remove(local_path)

    print(f"Downloading through Chrome: {pdf_url}")

    page = context.new_page()

    try:
        try:
            with page.expect_download(timeout=120000) as download_info:
                try:
                    page.goto(pdf_url, wait_until="domcontentloaded", timeout=120000)
                except Exception:
                    pass

            download = download_info.value
            download.save_as(local_path)

        except PlaywrightTimeoutError:
            raise RuntimeError("Chrome did not trigger a PDF download.")

        if not is_valid_pdf(local_path):
            if os.path.exists(local_path):
                os.remove(local_path)
            raise RuntimeError("Downloaded file was not a valid PDF.")

        print(f"Saved valid PDF: {local_path}")
        return local_path

    finally:
        page.close()


def extract_pdf_text(local_pdf_path):
    print(f"Running OCR for {local_pdf_path}")

    ocr_text = extract_text_with_ocr(local_pdf_path)

    if len(ocr_text.strip()) > 500:
        return ocr_text

    print("OCR was weak. Trying embedded text backup.")

    text_parts = []

    try:
        reader = PdfReader(local_pdf_path)
        for page in reader.pages:
            text = page.extract_text() or ""
            text_parts.append(text)
    except Exception as e:
        print(f"Embedded text extraction failed: {e}")

    return "\n".join(text_parts).strip()


def extract_text_with_ocr(local_pdf_path):
    ocr_text_parts = []

    try:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

        pages = convert_from_path(
            local_pdf_path,
            dpi=300,
            poppler_path=POPPLER_PATH
        )

        for i, page in enumerate(pages, start=1):
            print(f"OCR page {i}")
            text = pytesseract.image_to_string(page, config="--psm 6")
            ocr_text_parts.append(text)

    except Exception as e:
        print(f"OCR failed: {e}")

    return "\n".join(ocr_text_parts).strip()


def extract_with_ai(swg_number, pdf_url, full_text):
    prompt = f"""
You are extracting structured data from a USACE Galveston District Approved Jurisdictional Determination.

Return ONLY valid JSON. Do not include markdown.

Use null when information is not found.

Extract this structure:

{{
  "swg_number": "",
  "issue_date": "",
  "county": "",
  "state": "",
  "latitude": "",
  "longitude": "",
  "project_name": "",
  "applicant": "",
  "nearest_waterbody": "",
  "feature_summary": "",
  "jurisdictional_waters": "",
  "non_jurisdictional_features": "",
  "jurisdictional_reasoning": "",
  "features": [
    {{
      "feature_id": "",
      "feature_type": "",
      "jurisdictional_status": "",
      "basis": "",
      "reasoning": ""
    }}
  ]
}}

Known source:
SWG number from website link: {swg_number}
PDF URL: {pdf_url}

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
    except json.JSONDecodeError:
        print("AI returned invalid JSON. Saving raw response.")
        return {
            "swg_number": swg_number,
            "issue_date": None,
            "county": None,
            "state": None,
            "latitude": None,
            "longitude": None,
            "project_name": None,
            "applicant": None,
            "nearest_waterbody": None,
            "feature_summary": None,
            "jurisdictional_waters": None,
            "non_jurisdictional_features": None,
            "jurisdictional_reasoning": raw,
            "features": []
        }


def value(data, key):
    item = data.get(key)

    if item is None:
        return ""

    if isinstance(item, list):
        return json.dumps(item, ensure_ascii=False)

    return str(item)


def save_record(swg_number, pdf_url, local_pdf_path, full_ocr_text, extracted):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    now = datetime.now().isoformat(timespec="seconds")
    final_swg_number = value(extracted, "swg_number") or swg_number

    cur.execute("""
        INSERT INTO ajds (
            swg_number, pdf_url, local_pdf_path, issue_date,
            county, state, latitude, longitude, project_name, applicant,
            nearest_waterbody, feature_summary, jurisdictional_waters,
            non_jurisdictional_features, jurisdictional_reasoning,
            extracted_json, full_ocr_text, date_added, last_updated
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(swg_number) DO UPDATE SET
            pdf_url=excluded.pdf_url,
            local_pdf_path=excluded.local_pdf_path,
            issue_date=excluded.issue_date,
            county=excluded.county,
            state=excluded.state,
            latitude=excluded.latitude,
            longitude=excluded.longitude,
            project_name=excluded.project_name,
            applicant=excluded.applicant,
            nearest_waterbody=excluded.nearest_waterbody,
            feature_summary=excluded.feature_summary,
            jurisdictional_waters=excluded.jurisdictional_waters,
            non_jurisdictional_features=excluded.non_jurisdictional_features,
            jurisdictional_reasoning=excluded.jurisdictional_reasoning,
            extracted_json=excluded.extracted_json,
            full_ocr_text=excluded.full_ocr_text,
            last_updated=excluded.last_updated
    """, (
        final_swg_number,
        pdf_url,
        local_pdf_path,
        value(extracted, "issue_date"),
        value(extracted, "county"),
        value(extracted, "state"),
        value(extracted, "latitude"),
        value(extracted, "longitude"),
        value(extracted, "project_name"),
        value(extracted, "applicant"),
        value(extracted, "nearest_waterbody"),
        value(extracted, "feature_summary"),
        value(extracted, "jurisdictional_waters"),
        value(extracted, "non_jurisdictional_features"),
        value(extracted, "jurisdictional_reasoning"),
        json.dumps(extracted, ensure_ascii=False),
        full_ocr_text,
        now,
        now
    ))

    conn.commit()
    conn.close()


def main():
    setup_folders()
    setup_chrome_preferences()
    setup_database()

    links = get_2026_pdf_links()
    print(f"Found {len(links)} PDF links under 2026 Basis Forms.")

    if not links:
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=CHROME_PROFILE_DIR,
            channel="chrome",
            headless=False,
            accept_downloads=True,
            downloads_path=os.path.abspath(PDF_DIR)
        )

        for swg_number, pdf_url in links:
            print(f"\nProcessing {swg_number}")

            if already_processed(swg_number):
                print("Already processed. Skipping.")
                continue

            try:
                local_pdf_path = download_pdf_with_chrome(context, swg_number, pdf_url)
                full_text = extract_pdf_text(local_pdf_path)

                if not full_text:
                    print("No text extracted. Skipping.")
                    continue

                extracted = extract_with_ai(swg_number, pdf_url, full_text)
                save_record(swg_number, pdf_url, local_pdf_path, full_text, extracted)

            except Exception as e:
                print(f"Error processing {swg_number}: {e}")
                continue

        context.close()

    print("\nDone.")


if __name__ == "__main__":
    main()