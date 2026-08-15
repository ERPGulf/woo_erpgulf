"""
ERPNext <-> WooCommerce product reconciliation (Script Report).

One row per product. ERPNext values and LIVE Woo values sit side by side:
SKU | Item name (ERP) | Woo name | ERP stock | Woo stock | stock match
    | ERP price | Woo price | price match | Woo status | Woo id | Woo matches

Matching key: ERPNext `item_code`  ==  WooCommerce product `sku`.
(So it does NOT depend on any custom Woo-id field — that column is display only.)

Rules honoured:
- Read-only. Frappe ORM for the ERP side. WooCommerce REST API v3 for the Woo side.
- No writes anywhere. No raw SQL on ERPNext. No direct MariaDB access.

Place this folder at:
    <your_app>/<your_app>/report/woo_product_reconcile/
e.g. woo_erpgulf/woo_erpgulf/report/woo_product_reconcile/
Then `bench --site <site> migrate` (or reload) so ERPNext registers the report.
"""

import re

import frappe
from frappe import _

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


# =====================================================================
# CONFIG
# ---------------------------------------------------------------------
# NOTHING IS HARDCODED. The Woo URL + consumer key/secret (+ price list)
# are read live from the enabled "WooCommerce Server" record in
# woocommerce_fusion (the one with "Enable Sync" ticked).
WOO_SERVER_DOCTYPE = "WooCommerce Server"

# Selling price list for the ERP price column.
#   None  -> use the enabled WooCommerce Server's own `price_list` field
#   "..." -> override with a specific price list name
PRICE_LIST        = None
WOO_ID_FIELD      = "woocommerce_id"     # Item custom field holding Woo id (display only)

# Safety cap for live paging. 100 products per page -> 30 pages = 3000 products.
MAX_WOO_PAGES     = 60
PRICE_TOLERANCE   = 0.01                 # treat price diffs below this as equal
API_TIMEOUT       = 30                   # seconds per Woo API call

# Report checkbox filters -> Item field. Filter values are "Yes"/"No" (blank = all).
CHECK_FILTERS = {
    "f_disable_sync":          "custom_disable_sync",
    "f_vin_required":          "custom_vin_required",
    "f_disable_sync_no_stock": "custom_disable_sync_if_not_in_stock",
    "f_verified":              "custom_verified",
    "f_universal":             "custom_universal",
}
# =====================================================================


# credentials resolved once per report run
_WOO_SERVER_CACHE = {}


def _get_woo_server():
    """(base_url, consumer_key, consumer_secret, price_list) from the enabled
    WooCommerce Server record. Cached for the duration of the request."""
    if "val" in _WOO_SERVER_CACHE:
        return _WOO_SERVER_CACHE["val"]

    name = frappe.db.get_value(WOO_SERVER_DOCTYPE, {"enable_sync": 1}, "name")
    if not name:
        frappe.throw(_("No WooCommerce Server has 'Enable Sync' ticked."))

    doc = frappe.get_doc(WOO_SERVER_DOCTYPE, name)
    base = (doc.get("woocommerce_server_url") or "").rstrip("/")
    key = (doc.get("api_consumer_key") or "").strip()
    sec = (doc.get("api_consumer_secret") or "").strip()
    plist = PRICE_LIST or doc.get("price_list") or "Standard Selling"

    if not (base and key and sec):
        frappe.throw(_("Enabled WooCommerce Server '{0}' is missing URL / key / secret.").format(name))

    val = (base, key, sec, plist)
    _WOO_SERVER_CACHE["val"] = val
    return val


def execute(filters=None):
    filters = filters or {}
    items = get_items(filters)
    warehouses = get_branch_warehouses([i["item_code"] for i in items], filters)
    columns = get_columns(warehouses)
    data = get_data(filters, items, warehouses)
    return columns, data


def wh_fieldname(wh):
    return "wh_" + frappe.scrub(wh)


def get_columns(warehouses):
    # ERP columns (black) sit right next to their Woo counterpart (blue).
    cols = [
        {"label": _("SKU"),             "fieldname": "sku",         "fieldtype": "Data",     "width": 150},
        {"label": _("Diff on"),         "fieldname": "diff_on",     "fieldtype": "Data",     "width": 120},
        {"label": _("Sync note"),       "fieldname": "sync_note",   "fieldtype": "Data",     "width": 130},
        {"label": _("Item name (ERP)"), "fieldname": "erp_name",    "fieldtype": "Data",     "width": 200},
        {"label": _("Woo name"),        "fieldname": "woo_name",    "fieldtype": "Data",     "width": 200},
        {"label": _("ERP compat"),      "fieldname": "erp_compat",  "fieldtype": "Data",     "width": 220},
        {"label": _("Woo compat"),      "fieldname": "woo_compat",  "fieldtype": "Data",     "width": 220},
    ]
    # ---- per branch: ERP qty (black) next to Woo qty (blue) ----
    for wh in warehouses:
        cols.append({"label": wh + " (ERP)", "fieldname": "erpwh_" + frappe.scrub(wh), "fieldtype": "Float", "width": 115, "precision": 0})
        cols.append({"label": wh + " (Woo)", "fieldname": "woowh_" + frappe.scrub(wh), "fieldtype": "Float", "width": 115, "precision": 0})
    cols += [
        {"label": _("ERP stock (all)"), "fieldname": "erp_stock",   "fieldtype": "Float",    "width": 100, "precision": 0},
        {"label": _("Woo stock"),       "fieldname": "woo_stock",   "fieldtype": "Data",     "width": 90},
        {"label": _("Stock"),           "fieldname": "stock_match", "fieldtype": "Data",     "width": 60},
        {"label": _("ERP price"),       "fieldname": "erp_price",   "fieldtype": "Currency", "width": 100},
        {"label": _("Woo price"),       "fieldname": "woo_price",   "fieldtype": "Currency", "width": 100},
        {"label": _("Price"),           "fieldname": "price_match", "fieldtype": "Data",     "width": 60},
        {"label": _("Presence"),        "fieldname": "presence",    "fieldtype": "Data",     "width": 120},
        {"label": _("Woo status"),      "fieldname": "woo_status",  "fieldtype": "Data",     "width": 90},
        {"label": _("Woo id"),          "fieldname": "woo_id",      "fieldtype": "Data",     "width": 80},
        {"label": _("Woo matches"),     "fieldname": "woo_matches", "fieldtype": "Int",      "width": 90},
    ]
    return cols


def get_items(filters):
    item_filters = {}
    if filters.get("brand"):
        item_filters["brand"] = filters["brand"]
    if filters.get("item_group"):
        item_filters["item_group"] = filters["item_group"]
    if filters.get("sku"):
        item_filters["item_code"] = ["like", "%%%s%%" % filters["sku"]]
    if not filters.get("include_disabled"):
        item_filters["disabled"] = 0
    item_meta = frappe.get_meta("Item")
    for fkey, field in CHECK_FILTERS.items():
        v = filters.get(fkey)
        if v in ("Yes", "No") and item_meta.has_field(field):
            item_filters[field] = 1 if v == "Yes" else 0
    limit = int(filters.get("max_products") or 0)  # 0 = all items
    return frappe.get_all(
        "Item",
        filters=item_filters,
        fields=["item_code", "item_name", "custom_woo_name__arabic", "disabled",
                "custom_disable_sync", "custom_disable_sync_if_not_in_stock",
                WOO_ID_FIELD + " as woo_id"],
                order_by="item_code asc",
        limit_page_length=limit,
    )


def get_branch_warehouses(codes, filters):
    """Branch warehouses to show as columns: the specific one if filtered,
    else every warehouse that has a stock record for the shown items."""
    if filters.get("warehouse"):
        return [filters["warehouse"]]
    if not codes:
        return []
    rows = frappe.get_all(
        "Bin",
        filters={"item_code": ["in", codes]},
        fields=["warehouse"],
        group_by="warehouse",
        order_by="warehouse asc",
    )
    whs = [r["warehouse"] for r in rows if r.get("warehouse")]
    # drop group/parent warehouses (e.g. "All Warehouses") — they hold no stock
    if whs:
        groups = {g["name"] for g in frappe.get_all(
            "Warehouse", filters={"name": ["in", whs], "is_group": 1}, fields=["name"])}
        whs = [w for w in whs if w not in groups]
    return whs


def get_data(filters, items, warehouses):
    codes = [i["item_code"] for i in items]

    price_map = get_price_map(codes)
    stock_map = get_stock_map(codes)
    bundle_children_map = get_bundle_children_map(codes)              # KIT bundles
    bundle_stock_map = compute_bundle_stock_map(bundle_children_map)  # derived stock
    bundle_set = set(bundle_stock_map.keys())
    compat_map = get_erp_compat_map(codes)

    # items that have any Item WooCommerce Server row (needed for the "no server" reason)
    server_parents = {r["parent"] for r in frappe.get_all(
        "Item WooCommerce Server", filters={"parent": ["in", codes]}, fields=["parent"])} if codes else set()

    # ---- Woo products (LIVE REST) ----
    if filters.get("sku") and len(codes) <= 50:
        woo_map = {c: fetch_woo_one(c) for c in codes}
    else:
        woo_map = fetch_woo_map()

    scoped = bool(
        filters.get("sku") or filters.get("brand") or filters.get("item_group")
        or any(filters.get(k) in ("Yes", "No") for k in CHECK_FILTERS)
    )
    only_mismatch = bool(filters.get("only_mismatches"))
    rows = []

    for it in items:
        sku = it["item_code"]
        erp_name = it.get("custom_woo_name__arabic") or it.get("item_name") or ""
        erp_price = price_map.get(sku)
        is_bundle = sku in bundle_set
        if filters.get("only_bundles") and not is_bundle:
            continue
        # Bundles keep no own Bin stock -> use derived (per-branch min over children, summed)
        st = bundle_stock_map.get(sku, {}) if is_bundle else stock_map.get(sku, {})
        erp_stock = st.get("_total", 0.0)

        woo_list = woo_map.get(sku) or []
        woo = woo_list[0] if woo_list else None

        if woo:
            woo_name = woo.get("name") or ""
            woo_status = woo.get("status") or ""
            woo_id = woo.get("id")
            woo_stock_raw = woo.get("stock_quantity")
            woo_stock_disp = "—" if woo_stock_raw is None else woo_stock_raw
            woo_price = to_float(woo.get("regular_price") or woo.get("price"))
            presence = _("Both")
        else:
            woo_name = ""
            woo_status = ""
            woo_id = ""
            woo_stock_raw = None
            woo_stock_disp = "—"
            woo_price = None
            presence = _("ERP only")

        stock_match = compare_stock(erp_stock, woo_stock_raw)
        price_match = compare_price(erp_price, woo_price)
        diff_on = build_diff_on(presence, stock_match, price_match, erp_name, woo_name)

        erp_compat = compat_map.get(sku, "")
        woo_compat = woo_compat_summary(woo)

        note = sync_reason(it, erp_price, sku in server_parents)
        row = {
            "sku": sku,
            "diff_on": diff_on,
            "sync_note": note,
            "erp_name": erp_name,
            "woo_name": woo_name,
            "erp_compat": erp_compat,
            "woo_compat": woo_compat,
            "erp_stock": erp_stock,
            "woo_stock": woo_stock_disp,
            "stock_match": stock_match,
            "erp_price": erp_price,
            "woo_price": woo_price,
            "price_match": price_match,
            "presence": presence,
            "woo_status": woo_status,
            "woo_id": woo_id or it.get("woo_id") or "",
            "woo_matches": len(woo_list),
            "can_sync": 1 if diff_on else 0,   # ERP item that differs -> syncable
            "erp_syncable": 0 if note else 1,  # ERP item, nothing blocking a push (matched or not)
        }
        wb = woo_branch_stock(woo)
        for wh in warehouses:
            row["erpwh_" + frappe.scrub(wh)] = st.get(wh)
            row["woowh_" + frappe.scrub(wh)] = wb.get(woo_branch_slug(wh))

        if only_mismatch and not diff_on:
            continue
        rows.append(row)

    # ---- Woo-only SKUs (only in full-catalogue mode, no scoping filter) ----
    if not scoped:
        erp_set = set(codes)
        for sku, wlist in woo_map.items():
            if sku in erp_set:
                continue
            woo = wlist[0]
            r = {
                "sku": sku,
                "diff_on": _("Missing in ERP"),
                "sync_note": "",
                "erp_name": "",
                "woo_name": woo.get("name") or "",
                "erp_compat": "",
                "woo_compat": woo_compat_summary(woo),
                "erp_stock": None,
                "woo_stock": "—" if woo.get("stock_quantity") is None else woo.get("stock_quantity"),
                "stock_match": "—",
                "erp_price": None,
                "woo_price": to_float(woo.get("regular_price") or woo.get("price")),
                "price_match": "—",
                "presence": _("Woo only"),
                "woo_status": woo.get("status") or "",
                "woo_id": woo.get("id"),
                "woo_matches": len(wlist),
                "can_sync": 0,   # no ERP item -> can't push ERP->Woo
                "erp_syncable": 0,
            }
            wb = woo_branch_stock(woo)
            for wh in warehouses:
                r["erpwh_" + frappe.scrub(wh)] = None
                r["woowh_" + frappe.scrub(wh)] = wb.get(woo_branch_slug(wh))
            rows.append(r)

    return rows


def build_diff_on(presence, stock_match, price_match, erp_name, woo_name):
    """Short, sortable summary of what differs for this row."""
    if presence == _("ERP only"):
        return _("Missing in Woo")
    parts = []
    if stock_match == "✗":
        parts.append(_("Stock"))
    if price_match == "✗":
        parts.append(_("Price"))
    a, b = (erp_name or "").strip().lower(), (woo_name or "").strip().lower()
    if a and b and a != b:
        parts.append(_("Name"))
    return " + ".join(parts)   # empty string = everything matches


def sync_reason(it, erp_price, has_server):
    """Why the sync would skip this item (mirrors run_item_sync's checks).
    Empty string = nothing blocks it (ready to sync / already synced)."""
    if it.get("disabled"):
        return _("Item disabled")
    if it.get("custom_disable_sync") or it.get("custom_disable_sync_if_not_in_stock"):
        return _("Sync disabled")
    if not erp_price or float(erp_price) <= 0:
        return _("No price")
    if not has_server:
        return _("No Woo server row")
    return ""


# ---------------------------------------------------------------------
# Compatibility + per-branch stock (parsed from Woo ACF postmeta)
# ---------------------------------------------------------------------
COMPAT_MAX = 3   # vehicles to list before "+N"

# Optional overrides: ERP warehouse name -> Woo branch slug.
# If a warehouse isn't listed here, the slug is guessed as "<first word>-branch"
# (e.g. "Khobar Warehouse" -> "khobar-branch").
BRANCH_MAP = {
    # "Khobar Warehouse": "khobar-branch",
}


def woo_branch_slug(wh):
    if wh in BRANCH_MAP:
        return BRANCH_MAP[wh]
    tokens = re.split(r"[\s\-]+", (wh or "").strip())
    return (tokens[0].lower() + "-branch") if tokens and tokens[0] else ""


def _year_range(years):
    parts = [p.strip() for p in str(years or "").split(",") if p.strip()]
    nums = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            pass
    if nums and len(nums) == (max(nums) - min(nums) + 1):
        return "%d-%d" % (min(nums), max(nums))
    return parts[0] if parts else ""


def _woo_meta(woo):
    return {str(m.get("key")): m.get("value") for m in (woo.get("meta_data") or [])} if woo else {}


def _summarise(triples):
    """triples = [(brand, model, years), ...] -> 'Brand Model (years), ..., +N'."""
    labels = []
    for brand, model, years in triples:
        label = " ".join(p for p in [brand, model] if p).strip()
        yr = _year_range(years)
        if yr:
            label += " (%s)" % yr
        if label.strip():
            labels.append(label)
    n = len(labels)
    txt = ", ".join(labels[:COMPAT_MAX])
    if n > COMPAT_MAX:
        txt += ", +%d" % (n - COMPAT_MAX)
    return txt


def get_erp_compat_map(codes):
    """{item_code: summary} from Item.custom_compatibility (ERP, English)."""
    if not codes:
        return {}
    rows = frappe.get_all(
        "Compatibility",
        filters={"parenttype": "Item", "parentfield": "custom_compatibility", "parent": ["in", codes]},
        fields=["parent", "brand", "model", "years"],
        order_by="parent asc, idx asc",
    )
    grouped = {}
    for r in rows:
        grouped.setdefault(r["parent"], []).append(r)
    return {pid: _summarise([(r.get("brand"), r.get("model"), r.get("years")) for r in rws])
            for pid, rws in grouped.items()}


def woo_compat_summary(woo):
    """From the Woo 'add_compactable_details' ACF repeater postmeta (Arabic)."""
    meta = _woo_meta(woo)
    try:
        n = int(meta.get("add_compactable_details") or 0)
    except (TypeError, ValueError):
        n = 0
    triples = [(
        meta.get("add_compactable_details_%d_brand" % i),
        meta.get("add_compactable_details_%d_model" % i),
        meta.get("add_compactable_details_%d_years" % i),
    ) for i in range(n)]
    return _summarise(triples)


def woo_branch_stock(woo):
    """{branch_slug: qty} from the Woo 'branch_stock' ACF repeater postmeta."""
    meta = _woo_meta(woo)
    try:
        n = int(meta.get("branch_stock") or 0)
    except (TypeError, ValueError):
        n = 0
    out = {}
    for i in range(n):
        slug = meta.get("branch_stock_%d_branch" % i)
        if not slug:
            continue
        try:
            out[str(slug)] = float(meta.get("branch_stock_%d_stock_qty" % i) or 0)
        except (TypeError, ValueError):
            out[str(slug)] = 0.0
    return out


# ---------------------------------------------------------------------
# ERPNext helpers (ORM only)
# ---------------------------------------------------------------------
def get_price_map(codes):
    if not codes:
        return {}
    _b, _k, _s, price_list = _get_woo_server()
    rows = frappe.get_all(
        "Item Price",
        filters={"price_list": price_list, "item_code": ["in", codes]},
        fields=["item_code", "price_list_rate"],
    )
    out = {}
    for r in rows:
        # keep the first / highest rate if duplicates exist
        if r["item_code"] not in out:
            out[r["item_code"]] = r["price_list_rate"]
    return out


def get_stock_map(codes):
    """{item_code: {"_total": qty, "<warehouse>": qty, ...}} across all warehouses."""
    if not codes:
        return {}
    rows = frappe.get_all(
        "Bin",
        filters={"item_code": ["in", codes]},
        fields=["item_code", "warehouse", "actual_qty"],
    )
    out = {}
    for r in rows:
        d = out.setdefault(r["item_code"], {"_total": 0.0})
        q = r.get("actual_qty") or 0.0
        d[r["warehouse"]] = d.get(r["warehouse"], 0.0) + q
        d["_total"] += q
    return out


def get_bundle_children_map(codes):
    """{bundle_item_code: [{"item_code":.., "qty":..}, ...]} for ERPNext Product Bundles."""
    if not codes:
        return {}
    bundles = frappe.get_all(
        "Product Bundle",
        filters={"new_item_code": ["in", codes]},
        fields=["name", "new_item_code"],
    )
    if not bundles:
        return {}
    names = [b["name"] for b in bundles]
    child_rows = frappe.get_all(
        "Product Bundle Item",
        filters={"parent": ["in", names]},
        fields=["parent", "item_code", "qty"],
        order_by="parent asc, idx asc",
    )
    by_parent = {}
    for r in child_rows:
        by_parent.setdefault(r["parent"], []).append(
            {"item_code": r["item_code"], "qty": (r.get("qty") or 1)}
        )
    return {b["new_item_code"]: by_parent.get(b["name"], []) for b in bundles}


def compute_bundle_stock_map(bundle_children_map):
    """Derived buyable stock per bundle, reproducing woocommerce_fusion sync_items.py:
       per warehouse = min over children that HAVE stock there of floor(qty / line_qty);
       _total (what the sync pushes as stock_quantity) = sum of per-branch values > 0."""
    if not bundle_children_map:
        return {}
    child_codes = sorted({
        kid["item_code"] for kids in bundle_children_map.values() for kid in kids
    })
    child_stock = get_stock_map(child_codes)
    out = {}
    for bcode, kids in bundle_children_map.items():
        if not kids:
            out[bcode] = {"_total": 0.0}
            continue
        all_whs = set()
        for kid in kids:
            for wh in child_stock.get(kid["item_code"], {}):
                if wh != "_total":
                    all_whs.add(wh)
        row = {}
        for wh in all_whs:
            # Strict per-branch: every child must have enough in this branch, else 0.
            buildable = None
            for kid in kids:
                req = kid.get("qty") or 1
                if req <= 0:
                    req = 1
                avail = child_stock.get(kid["item_code"], {}).get(wh, 0) or 0
                q = int(avail // req) if avail > 0 else 0
                buildable = q if buildable is None else min(buildable, q)
            buildable = buildable or 0
            if buildable > 0:
                row[wh] = float(buildable)
        # Overall stock must match woosb (company-wide): min over children of
        # floor(total child stock / qty). Per-branch values above match branch_stock.
        comp = None
        for kid in kids:
            req = kid.get("qty") or 1
            if req <= 0:
                req = 1
            tot = child_stock.get(kid["item_code"], {}).get("_total", 0) or 0
            q = int(tot // req) if tot > 0 else 0
            comp = q if comp is None else min(comp, q)
        row["_total"] = float(comp or 0)
        out[bcode] = row
    return out


# ---------------------------------------------------------------------
# WooCommerce helpers (REST v3, read-only)
#   >>> To reuse your woo_erpgulf client, replace the bodies below. <<<
# ---------------------------------------------------------------------
def _woo_get(path, params=None):
    if requests is None:
        frappe.throw(_("The 'requests' library is not available on this bench."))
    base, key, sec, _plist = _get_woo_server()
    resp = requests.get(
        base + path,
        params=params or {},
        auth=(key, sec),
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# Only pull the fields the report actually uses -> far smaller/faster responses.
WOO_FIELDS = "id,sku,name,price,regular_price,stock_quantity,status,meta_data"


def fetch_woo_one(sku):
    """Fetch products matching a single SKU (may return >1 due to WPML translations)."""
    try:
        return _woo_get("/wp-json/wc/v3/products", {"sku": sku, "per_page": 100, "_fields": WOO_FIELDS})
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "woo_product_reconcile fetch_woo_one")
        return []


def fetch_woo_map():
    """Page through all Woo products once -> {sku: [product, ...]}."""
    out = {}
    page = 1
    while page <= MAX_WOO_PAGES:
        try:
            batch = _woo_get(
                "/wp-json/wc/v3/products",
                {"per_page": 100, "page": page, "_fields": WOO_FIELDS, "status": "publish", "orderby": "id", "order": "asc"},
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "woo_product_reconcile fetch_woo_map")
            break
        if not batch:
            break
        for p in batch:
            sku = (p.get("sku") or "").strip()
            if not sku:
                continue
            out.setdefault(sku, []).append(p)
        if len(batch) < 100:
            break
        page += 1
    return out


# ---------------------------------------------------------------------
# comparison helpers
# ---------------------------------------------------------------------
def to_float(val):
    try:
        if val in (None, ""):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def compare_stock(erp_stock, woo_stock_raw):
    if woo_stock_raw is None:
        return "—"          # Woo not managing stock / product absent
    try:
        return "✓" if float(erp_stock or 0) == float(woo_stock_raw) else "✗"
    except (TypeError, ValueError):
        return "—"


def compare_price(erp_price, woo_price):
    if erp_price is None or woo_price is None:
        return "—"
    return "✓" if abs(float(erp_price) - float(woo_price)) <= PRICE_TOLERANCE else "✗"


# =====================================================================
# Vehicle fitment rebuild — triggers the WooCommerce site to rebuild the
# fitment index + vehicles.csv + lookup. Read-only on ERP; the only write
# is a POST to the store's own REST endpoint (Application Password auth).
# =====================================================================
WOO_SYNC_IDLE_SECONDS = 300   # a Woo bulk sync is "running" if a log row was written in the last 5 min


def _wp_base_and_auth():
    """Reuse the enabled WooCommerce Server's URL + consumer key/secret (the same
    creds woocommerce_fusion already syncs with). No separate app password needed."""
    base, key, sec, _plist = _get_woo_server()
    return base, key, sec


@frappe.whitelist()
def is_woo_sync_running():
    """True if a Woo bulk sync looks active (newest Woo Sync Log row < 5 min old)."""
    last = frappe.db.get_value("Woo Sync Log", {}, "creation", order_by="creation desc")
    if not last:
        return {"running": False, "idle_seconds": None}
    secs = frappe.utils.time_diff_in_seconds(frappe.utils.now_datetime(), frappe.utils.get_datetime(last))
    return {"running": secs < WOO_SYNC_IDLE_SECONDS, "idle_seconds": int(secs)}


@frappe.whitelist()
def is_fitment_rebuild_running():
    """Ask the WooCommerce site whether a fitment rebuild is in progress."""
    try:
        base, key, sec = _wp_base_and_auth()
        r = requests.get(f"{base}/wp-json/erpgulf/v1/rebuild-status", auth=(key, sec), timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        data["configured"] = True
        return data
    except Exception as e:
        return {"running": False, "configured": True, "error": str(e)}


@frappe.whitelist()
def rebuild_woo_fitments(csv=1, lookup=1):
    """Queue a full fitment rebuild on the store (table + vehicles.csv + lookup).
    Refuses while a Woo sync is still running so the two never collide."""
    sync = is_woo_sync_running()
    if sync.get("running"):
        frappe.throw("A WooCommerce sync is still running (last activity %ss ago). "
                     "Let it finish, then rebuild." % sync.get("idle_seconds"))
    base, key, sec = _wp_base_and_auth()
    try:
        r = requests.post(f"{base}/wp-json/erpgulf/v1/rebuild-fitments", auth=(key, sec),
                          json={"csv": bool(int(csv)), "lookup": bool(int(lookup))}, timeout=30)
    except Exception as e:
        frappe.throw("Could not reach WooCommerce: %s" % e)
    if r.status_code == 409:
        return {"queued": False, "running": True, "message": "A rebuild is already running on WooCommerce."}
    if r.status_code not in (200, 202):
        frappe.throw("WooCommerce returned %s: %s" % (r.status_code, r.text[:300]))
    try:
        payload = r.json()
    except Exception:
        payload = {"queued": True, "running": True, "message": "Rebuild queued."}
    # Record the outcome even if the user closes the browser: a background worker
    # polls the store until the rebuild finishes and logs it to the Error Log.
    frappe.enqueue(
        "woocommerce_fusion.woocommerce.report.woo_product_reconcile.woo_product_reconcile.watch_fitment_rebuild",
        queue="long", timeout=2400, now=False,
    )
    return payload


def watch_fitment_rebuild():
    """Runs on an ERP worker (not the browser). Polls the store until the fitment
    rebuild finishes, then records the result in the Error Log for an audit trail."""
    import time
    deadline = time.time() + 1800  # 30 min ceiling
    while time.time() < deadline:
        time.sleep(15)
        st = is_fitment_rebuild_running() or {}
        if st.get("error"):
            frappe.log_error("Woo Fitment Rebuild", "Status check failed: %s" % st.get("error"))
            return
        if not st.get("running"):
            res = st.get("result") or {}
            msg = res.get("message") or "done"
            ok = res.get("success", True)
            frappe.log_error("Woo Fitment Rebuild",
                             ("Finished (OK): " if ok else "Finished (FAILED): ") + str(msg))
            return
    frappe.log_error("Woo Fitment Rebuild", "Timed out waiting for rebuild to finish (30 min).")
