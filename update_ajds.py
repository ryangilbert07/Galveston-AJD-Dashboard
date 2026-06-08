import os
import re
import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from scrape_ajds import (
    BASE_URL,
    JD_PAGE_HTML,
    CHROME_PROFILE_DIR,
    setup_folders,
    setup_chrome_preferences,
    setup_database,
    download_pdf_with_chrome,
    extract_pdf_text,
    extract_with_ai,
    save_record,
)

from enrich_ajds import (
    setup_new_columns,
    extract_extra_fields,
    update_record,
)


CUTOFF_BASIS_YEAR = 2025
IGNORE_LIST_FILE = "ignore_list.txt"


def load_ignore_list():
    if not os.path.exists(IGNORE_LIST_FILE):
        return set()

    with open(IGNORE_LIST_FILE, "r", encoding="utf-8") as f:
        return {
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        }

def get_existing_swg_numbers():
    conn = sqlite3.connect("data/ajds.sqlite")
    cur = conn.cursor()

    cur.execute("SELECT swg_number FROM ajds")
    existing = {row[0] for row in cur.fetchall()}

    conn.close()
    return existing


def get_pdf_links_from_basis_sections():
    if not os.path.exists(JD_PAGE_HTML):
        raise FileNotFoundError(
            "jd_page.html was not found. Save the USACE JD page as jd_page.html in this project folder."
        )

    with open(JD_PAGE_HTML, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    links = []
    current_section_year = None

    for element in soup.find_all(["h2", "h3", "h4", "a"]):
        text = element.get_text(strip=True)

        heading_match = re.search(r"(\d{4})\s+Basis Forms", text)

        if element.name in ["h2", "h3", "h4"] and heading_match:
            current_section_year = int(heading_match.group(1))
            continue

        if current_section_year is None:
            continue

        if current_section_year < CUTOFF_BASIS_YEAR:
            continue

        if element.name == "a":
            href = element.get("href", "")

            if ".pdf" not in href.lower():
                continue

            pdf_url = urljoin(BASE_URL, href)

            swg_match = re.search(r"SWG[-_ ]?\d{4}[-_ ]?\d+", text, re.IGNORECASE)

            if not swg_match:
                swg_match = re.search(r"SWG[-_ ]?\d{4}[-_ ]?\d+", pdf_url, re.IGNORECASE)

            if swg_match:
                swg_number = swg_match.group(0).replace("_", "-").replace(" ", "-")
            else:
                swg_number = text or os.path.basename(pdf_url)

            links.append(
                {
                    "swg_number": swg_number,
                    "pdf_url": pdf_url,
                    "basis_year": current_section_year,
                }
            )

    unique = []
    seen_urls = set()

    for item in links:
        if item["pdf_url"] not in seen_urls:
            seen_urls.add(item["pdf_url"])
            unique.append(item)

    return unique


def main():
    setup_folders()
    setup_chrome_preferences()
    setup_database()
    setup_new_columns()

    all_links = get_pdf_links_from_basis_sections()
    existing = get_existing_swg_numbers()
    ignore_list = load_ignore_list()

    missing = [
        item for item in all_links
        if item["swg_number"] not in existing
        and item["swg_number"] not in ignore_list
    ]

    print(f"Found {len(all_links)} PDF links from {CUTOFF_BASIS_YEAR}+ Basis Forms sections.")
    print(f"Already in database: {len(all_links) - len(missing)}")
    print(f"New/missing to process: {len(missing)}")
    print(f"Ignored by ignore_list.txt: {len(ignore_list)}")

    if not missing:
        print("No new AJDs to process.")
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=CHROME_PROFILE_DIR,
            channel="chrome",
            headless=False,
            accept_downloads=True,
            downloads_path=os.path.abspath("pdfs")
        )

        for item in missing:
            swg_number = item["swg_number"]
            pdf_url = item["pdf_url"]

            print(f"\nProcessing new AJD: {swg_number}")

            try:
                local_pdf_path = download_pdf_with_chrome(context, swg_number, pdf_url)

                full_text = extract_pdf_text(local_pdf_path)

                if not full_text:
                    print(f"No OCR text extracted for {swg_number}. Skipping.")
                    continue

                extracted = extract_with_ai(swg_number, pdf_url, full_text)

                save_record(
                    swg_number,
                    pdf_url,
                    local_pdf_path,
                    full_text,
                    extracted
                )

                extra_fields = extract_extra_fields(swg_number, full_text)
                update_record(swg_number, extra_fields)

                print(f"Saved and enriched {swg_number}")

            except Exception as e:
                print(f"Error processing {swg_number}: {e}")
                continue

        context.close()

    print("\nUpdate complete.")


if __name__ == "__main__":
    main()