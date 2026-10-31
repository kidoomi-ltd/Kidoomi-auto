"""Identity aliases for a fresh store: Kidoomi has no platform-corrupted SKUs (yet), so both maps are empty and every SKU
passes through unchanged. The interface matches the real module (YRA/GTV carry CSV-driven maps + probe_sku_rounding.py);
the moment Kidoomi ever grows a mangled SKU, port the real module and its probe.
"""

TO_ONBUY = {}
TO_TRUE = {}


def to_onbuy(sku):
    return TO_ONBUY.get(sku, sku)


def to_true(sku):
    return TO_TRUE.get(sku, sku)
