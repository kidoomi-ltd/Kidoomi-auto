"""One-off: create the store's spreadsheet (Kidoomi_Full_Feed_Master), share it with the owner, and give its product tab
("eBay") the SAME header row as the template store's product tab - so every tool reads identical columns from day one.
Idempotent: an existing book/tab/header is completed, never overwritten; nothing but row 1 of the product tab is written.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry

SHEET_NAME = "Kidoomi_Full_Feed_Master"
PRODUCT_TAB = (os.getenv("PRODUCT_TAB") or "eBay").strip()
TEMPLATE_SHEET = (os.getenv("TEMPLATE_SHEET") or "YRA_Full_Feed_Master").strip()
TEMPLATE_TAB = (os.getenv("TEMPLATE_TAB") or "Sheet1").strip()
SHARE_WITH = (os.getenv("SHARE_WITH") or "").strip()


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    gc = with_retry(lambda: gspread.authorize(creds), what="authorize", max_attempts=3)

    try:
        book = gc.open(SHEET_NAME)
        created = False
    except gspread.SpreadsheetNotFound:
        book = gc.create(SHEET_NAME)
        created = True
    print(f"book '{SHEET_NAME}': {'CREATED' if created else 'existed'} - https://docs.google.com/spreadsheets/d/{book.id}")

    if SHARE_WITH:
        try:
            book.share(SHARE_WITH, perm_type="user", role="writer", notify=True)
            print(f"shared as editor with {SHARE_WITH}")
        except Exception as exc:                                  # noqa: BLE001 - sharing twice answers an API error; the run still succeeded
            print(f"share: {str(exc)[:160]} (already shared is fine)")

    template_header = []
    try:
        template = gc.open(TEMPLATE_SHEET).worksheet(TEMPLATE_TAB)
        template_header = [str(h).strip() for h in template.row_values(1)]
        print(f"template '{TEMPLATE_SHEET}'/'{TEMPLATE_TAB}': {len(template_header)} header cells")
    except Exception as exc:                                      # noqa: BLE001
        print(f"template unreadable ({str(exc)[:120]}) - the header must come from a later run")

    tabs = book.worksheets()
    try:
        tab = book.worksheet(PRODUCT_TAB)
    except gspread.exceptions.WorksheetNotFound:
        first = tabs[0]
        if len(tabs) == 1 and not any(first.row_values(1)):
            first.update_title(PRODUCT_TAB)                       # the fresh book's empty default tab becomes the product tab
            tab = first
        else:
            tab = book.add_worksheet(title=PRODUCT_TAB, rows=4000, cols=max(len(template_header) + 2, 40))
    existing = [str(h).strip() for h in tab.row_values(1)]
    header = [h for h in existing if h] if any(existing) else list(template_header)
    for col in template_header:
        if col and col not in header:
            header.append(col)
    if header and header != existing:
        if tab.col_count < len(header):
            tab.resize(cols=len(header) + 2)
        tab.update(range_name="A1", values=[header])
        print(f"tab '{PRODUCT_TAB}': header written ({len(header)} cells)")
    else:
        print(f"tab '{PRODUCT_TAB}': header already complete ({len(header)} cells)")
    print("Done. Next: the Setup Amazon Tab workflow gives the Amazon tab the same base header plus the Keepa columns.")


if __name__ == "__main__":
    main()
