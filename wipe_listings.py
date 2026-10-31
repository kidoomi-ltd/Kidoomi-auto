"""Empty the Kidoomi OnBuy account (the user's order, 2026-10-31: the store starts clean before the sync is built).

Three stages, every run:
  1. SWEEP   - every listing, paged; the full table (sku, opc, name, price, stock) lands in kidoomi_listings_export.csv first,
               so nothing is deleted that was not exported. The export is the backup.
  2. DELETE  - only when DRY_RUN=0 AND CONFIRM=WIPE: batched DELETE /listings/by-sku (a 10-SKU canary batch first), per-item
               outcomes kept; suspended listings always refuse the API and land in suspended_skus.txt for the owner's hand.
  3. VERIFY  - a fresh sweep; the run ends by saying what is left (suspended leftovers are expected, anything else is named).

DRY_RUN=1 (the default) does stage 1 only and prints what stage 2 would do.
"""
import csv
import os
import sys
import time

import requests

from onbuy_client import BASE_URL, OnBuyClient

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false")
CONFIRM = (os.getenv("CONFIRM") or "").strip()
MAX_DELETE = int(os.getenv("MAX_DELETE") or 25000)
BATCH = int(os.getenv("DELETE_BATCH") or 100)


def sweep(onbuy):
    rows, offset = [], 0
    while True:
        resp = onbuy._send("GET", f"{BASE_URL}/listings", what=f"listings page {offset}",
                           params={"site_id": onbuy.site_id, "limit": 100, "offset": offset}, timeout=60)
        if resp.status_code != 200:
            raise SystemExit(f"GET /listings answered HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        onbuy._listings_total = (body.get("metadata") or {}).get("total_rows") if isinstance(body, dict) else None
        page = body.get("results") if isinstance(body, dict) else body
        page = page if isinstance(page, list) else []
        for it in page:
            it = it or {}
            rows.append({"sku": str(it.get("sku") or "").strip(), "opc": str(it.get("opc") or "").strip(),
                         "name": str(it.get("name") or "")[:160], "price": it.get("price"), "stock": it.get("stock")})
        if not onbuy.more_listings(offset, 100) or offset > 60000:
            break
        offset += 100
        time.sleep(0.2)
    return [r for r in rows if r["sku"]]


def send_delete(onbuy, skus):
    """One DELETE batch; waits out rate limits, retries server errors (the YRA batch deleter's manners)."""
    errors = 0
    for _ in range(60):
        try:
            resp = onbuy._send("DELETE", f"{BASE_URL}/listings/by-sku", what=f"delete batch({len(skus)})",
                               json={"site_id": onbuy.site_id, "skus": skus}, timeout=180)
        except requests.RequestException as exc:
            errors += 1
            if errors >= 4:
                return None, f"network: {str(exc)[:160]}"
            time.sleep(20)
            continue
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After") or 0) if str(resp.headers.get("Retry-After") or "").isdigit() else 0
            print(f"rate limited - waiting {max(wait, 90)}s")
            time.sleep(max(wait, 90))
            continue
        if resp.status_code >= 500:
            errors += 1
            if errors >= 4:
                return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
            time.sleep(20)
            continue
        try:
            return resp.json(), ""
        except ValueError:
            return {}, ""
    return None, "rate limit never cleared"


def outcomes_of(body, skus):
    """{sku: outcome word} from a delete answer: '' = deleted; 'suspended'/'gone'/the error text otherwise."""
    results = (body or {}).get("results") if isinstance(body, dict) else None
    if isinstance(results, list):
        results = {str((it or {}).get("sku") or "").strip(): it for it in results}
    out = {}
    for sku in skus:
        it = (results or {}).get(sku) if isinstance(results, dict) else None
        err = str(((it or {}).get("error") or (it or {}).get("message") or "")).strip() if isinstance(it, dict) else ""
        if isinstance(it, dict) and it.get("success") is True:
            err = ""
        low = err.lower()
        if not err:
            out[sku] = ""
        elif "suspend" in low:
            out[sku] = "suspended"
        elif "already" in low or "not exist" in low or "not found" in low:
            out[sku] = "gone"
        else:
            out[sku] = err[:140]
    return out


def main():
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("FAILED to authenticate with OnBuy")
    rows = sweep(onbuy)
    with open("kidoomi_listings_export.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["sku", "opc", "name", "price", "stock"])
        w.writeheader()
        w.writerows(rows)
    print(f"SWEEP: {len(rows)} listing(s) on the account - all exported to kidoomi_listings_export.csv (the backup)")
    for r in rows[:5]:
        print(f"   e.g. {r['sku']} | {r['name'][:60]} | price {r['price']} stock {r['stock']}")
    if DRY_RUN:
        print(f"DRY RUN - nothing deleted. A live run would delete {min(len(rows), MAX_DELETE)} listing(s) (cap {MAX_DELETE}).")
        return
    if CONFIRM != "WIPE":
        raise SystemExit("A live run needs confirm=WIPE typed into the workflow input - this deletes EVERY listing on the account.")
    if len(rows) > MAX_DELETE:
        raise SystemExit(f"{len(rows)} listings is above the max_delete cap {MAX_DELETE} - raise the cap to mean it.")

    todo = [r["sku"] for r in rows]
    outcomes = {}
    batches = [todo[:10]] + [todo[i:i + BATCH] for i in range(10, len(todo), BATCH)] if len(todo) > 10 else [todo]
    for n, chunk in enumerate(batches, 1):
        body, err = send_delete(onbuy, chunk)
        if body is None:
            print(f"batch {n}: FAILED - {err}; stopping here (what was deleted stays deleted, rerun for the rest)")
            break
        got = outcomes_of(body, chunk)
        outcomes.update(got)
        bad = sum(1 for v in got.values() if v not in ("", "gone"))
        print(f"batch {n}: {len(chunk)} sent | deleted {sum(1 for v in got.values() if v == '')} | gone {sum(1 for v in got.values() if v == 'gone')} | refused {bad}")
        time.sleep(1)

    suspended = sorted(s for s, v in outcomes.items() if v == "suspended")
    with open("delete_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "outcome"])
        w.writerows(sorted((s, v or "deleted") for s, v in outcomes.items()))
    with open("suspended_skus.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(suspended) + ("\n" if suspended else ""))
    print(f"DONE: {sum(1 for v in outcomes.values() if v == '')} deleted, {sum(1 for v in outcomes.values() if v == 'gone')} already gone, "
          f"{len(suspended)} suspended (only a person can delete those - suspended_skus.txt), "
          f"{sum(1 for v in outcomes.values() if v not in ('', 'gone', 'suspended'))} other refusals")

    left = sweep(onbuy)
    print(f"VERIFY: {len(left)} listing(s) still on the account")
    for r in left[:20]:
        print(f"   left: {r['sku']} ({outcomes.get(r['sku'], 'never answered')})")


if __name__ == "__main__":
    sys.exit(main())
