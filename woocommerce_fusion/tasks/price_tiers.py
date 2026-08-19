"""
Quantity-break promo tiers for adv-motors (ERPNext -> WooCommerce).

Reads selling Pricing Rules (qty slabs) for an item and pushes them to a CENTRAL
WooCommerce table (wp_adv_promo_tiers) keyed by SKU — NOT to product meta. One row
per SKU serves both the Arabic product and its English WPML twin, survives
re-translation, and carries the promo window so the storefront self-expires.

Place this file at:
    woocommerce_fusion/tasks/price_tiers.py

MANUAL ONLY. Promo tiers reach Woo exclusively via the Pricing Rule list action
"Sync Promotion to Woo" (sync_pricing_rules_to_woo). No doc_event hook; routine item
syncs never touch promo data. The action POSTs rows to:
    POST /wp-json/erpgulf/v1/promo-tiers   {"rows":[{sku,tiers,valid_from,valid_upto,active}, ...]}

Deactivation: we do NOT delete rows (keeps the audit trail). A rule that's disabled /
invalid / out of scope pushes {sku, active:0} (tiers kept on the row); a valid rule
pushes tiers + window + active:1.
"""

import json

import frappe
from frappe.utils import nowdate

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

API_TIMEOUT = 30
PROMO_ENDPOINT = "/wp-json/erpgulf/v1/promo-tiers"


def build_promo_row(item_code, item_group=None, brand=None):
    """Promo payload for an item, or None if it has no qualifying rule.
        {'tiers': [{'min':int,'max':int|None,'price':float}, ...],
         'valid_from': 'YYYY-MM-DD'|None, 'valid_upto': 'YYYY-MM-DD'|None}

    A Pricing Rule qualifies only if it is safe to mirror on the storefront:
      Selling, not Disabled, Price discount, Rate or Discount %, blanket (no
      'Applicable For' customer scope), NOT Mixed Conditions, NOT Cumulative, NOT
      coupon-based, qty-based (Min/Max Amt = 0), in the store currency, and scoped to
      this item (Item Code / Item Group / Brand). Rate wins; else Discount % off the
      item's Standard Selling price. The date window is passed through so WooCommerce
      enforces it (no date filtering here)."""
    if not item_code:
        return None

    if item_group is None or brand is None:
        vals = frappe.db.get_value("Item", item_code, ["item_group", "brand"], as_dict=True) or {}
        item_group = item_group if item_group is not None else vals.get("item_group")
        brand = brand if brand is not None else vals.get("brand")

    company = (frappe.defaults.get_user_default("Company")
               or frappe.db.get_single_value("Global Defaults", "default_company"))
    store_ccy = frappe.get_cached_value("Company", company, "default_currency") if company else None

    names = set()
    for r in frappe.get_all("Pricing Rule Item Code", filters={"item_code": item_code}, fields=["parent"]):
        names.add(r["parent"])
    if item_group:
        for r in frappe.get_all("Pricing Rule Item Group", filters={"item_group": item_group}, fields=["parent"]):
            names.add(r["parent"])
    if brand:
        for r in frappe.get_all("Pricing Rule Brand", filters={"brand": brand}, fields=["parent"]):
            names.add(r["parent"])
    if not names:
        return None

    rules = frappe.get_all(
        "Pricing Rule",
        filters={
            "name": ["in", list(names)],
            "selling": 1,
            "disable": 0,
            "price_or_product_discount": "Price",
            "mixed_conditions": 0,
            "is_cumulative": 0,
            "coupon_code_based": 0,
        },
                fields=["rate_or_discount", "rate", "discount_percentage",
                "min_qty", "max_qty", "min_amt", "max_amt",
                "applicable_for", "currency", "valid_from", "valid_upto",
                "rule_description", "promotional_scheme_id", "promotional_scheme"],
    )
    if not rules:
        return None

    base_rows = frappe.get_all(
        "Item Price",
        filters={"item_code": item_code, "price_list": "Standard Selling"},
        fields=["price_list_rate"], limit=1,
    )
    base = float(base_rows[0].price_list_rate) if base_rows else 0.0

    tiers, vfroms, vuptos = [], [], []
    for r in rules:
        if r.get("applicable_for"):                       # customer/territory scoped -> not blanket
            continue
        if store_ccy and r.get("currency") and r["currency"] != store_ccy:
            continue
        if float(r.get("min_amt") or 0) or float(r.get("max_amt") or 0):
            continue                                      # amount-based, not a qty tier
        mn = int(r.get("min_qty") or 0)
        mx = int(r.get("max_qty") or 0)
        if mn <= 0 and mx <= 0:
            continue                                      # not a qty-break rule
        if r.get("rate_or_discount") == "Rate" and r.get("rate"):
            price = float(r["rate"])
            # PACK row (Max Qty = 0): Rate holds the WHOLE-PACK price -> store per-unit.
            if mx <= 0 and mn > 0:
                price = price / mn
        elif r.get("rate_or_discount") == "Discount Percentage" and r.get("discount_percentage") and base:
            price = round(base * (1 - float(r["discount_percentage"]) / 100.0), 2)
        else:
            continue
                # Label (BOX/CARTON) lives on the Promotional Scheme slab, not the rule.
        # Try, in order: the rule's own field, the linked slab by id, then match
        # the slab by scheme + min/max qty (survives id-linking quirks).
        label = (r.get("rule_description") or "").strip()
        if not label and r.get("promotional_scheme_id"):
            label = (frappe.db.get_value("Promotional Scheme Price Discount",
                                         r["promotional_scheme_id"], "rule_description") or "").strip()
        if not label and r.get("promotional_scheme"):
            label = (frappe.db.get_value(
                "Promotional Scheme Price Discount",
                {"parent": r["promotional_scheme"],
                 "min_qty": r.get("min_qty") or 0,
                 "max_qty": r.get("max_qty") or 0},
                "rule_description") or "").strip()
        tiers.append({"min": mn if mn > 0 else 1, "max": (mx if mx > 0 else None), "price": price, "label": label})
        if r.get("valid_from"):
            vfroms.append(str(r["valid_from"])[:10])
        if r.get("valid_upto"):
            vuptos.append(str(r["valid_upto"])[:10])

    if not tiers:
        return None
    tiers.sort(key=lambda t: t["min"])
    return {
        "tiers": tiers,
        "valid_from": min(vfroms) if vfroms else None,   # earliest start
        "valid_upto": max(vuptos) if vuptos else None,   # latest end
    }


# ---------------------------------------------------------------------
# Manual push (Pricing Rule list -> Actions -> "Sync Promotion to Woo")
# ---------------------------------------------------------------------
def _woo_base_auth():
    name = frappe.db.get_value("WooCommerce Server", {"enable_sync": 1}, "name")
    if not name:
        frappe.throw("No WooCommerce Server has 'Enable Sync' ticked.")
    doc = frappe.get_doc("WooCommerce Server", name)
    base = (doc.get("woocommerce_server_url") or "").rstrip("/")
    return base, doc.get("api_consumer_key"), doc.get("api_consumer_secret")


def _items_for_rules(names):
    """All item codes scoped by the given Pricing Rules (Item Code / Group / Brand)."""
    codes = set()
    for rule_name in names:
        try:
            doc = frappe.get_doc("Pricing Rule", rule_name)
        except Exception:
            continue
        for row in (doc.get("items") or []):
            ic = row.get("item_code")
            if ic:
                codes.add(ic)
        for row in (doc.get("item_groups") or []):
            ig = row.get("item_group")
            if ig:
                for it in frappe.get_all("Item", filters={"item_group": ig, "disabled": 0}, fields=["item_code"]):
                    codes.add(it["item_code"])
        for row in (doc.get("brands") or []):
            br = row.get("brand")
            if br:
                for it in frappe.get_all("Item", filters={"brand": br, "disabled": 0}, fields=["item_code"]):
                    codes.add(it["item_code"])
    return codes


@frappe.whitelist()
def sync_pricing_rules_to_woo(names):
    """MANUAL. Push qty-break promo tiers for the items scoped by the selected Pricing
    Rules to the WooCommerce central table (one POST, keyed by SKU). Valid rule ->
    tiers + window + active:1; no valid rule -> active:0 (row kept for tracking).
    Only writes to WooCommerce via its REST endpoint (no MariaDB writes)."""
    if isinstance(names, str):
        names = json.loads(names)
    names = [n for n in (names or []) if n]
    if not names:
        return {"sent": 0, "items": []}
    if requests is None:
        frappe.throw("The 'requests' library is not available on this bench.")

    codes = _items_for_rules(names)
    if not codes:
        return {"sent": 0, "items": []}

    rows = []
    for code in sorted(codes):
        row = build_promo_row(code)
        if row and row.get("tiers"):
            rows.append({
                "sku": code,
                "tiers": row["tiers"],
                "valid_from": row.get("valid_from"),
                "valid_upto": row.get("valid_upto"),
                "active": 1,
            })
        else:
            rows.append({"sku": code, "active": 0})   # keep row, deactivate

    base, key, sec = _woo_base_auth()
    try:
        resp = requests.post(base + PROMO_ENDPOINT, auth=(key, sec),
                             json={"rows": rows}, timeout=API_TIMEOUT)
    except Exception as e:
        frappe.throw("Could not reach WooCommerce: %s" % e)
    if resp.status_code not in (200, 201):
        frappe.throw("WooCommerce returned %s: %s" % (resp.status_code, resp.text[:300]))

    try:
        updated = (resp.json() or {}).get("updated", len(rows))
    except Exception:
        updated = len(rows)
    return {"sent": len(rows), "updated": updated, "items": [r["sku"] for r in rows]}