"""Identity stub for a fresh store: Kidoomi has no corrupted-SKU aliases (yet). The other stores translate at this boundary
(see YRA's sku_aliases.py for the story); the moment Kidoomi grows a platform-mangled SKU, port the real module + probe."""


def to_onbuy(sku):
    return sku


def to_true(sku):
    return sku
