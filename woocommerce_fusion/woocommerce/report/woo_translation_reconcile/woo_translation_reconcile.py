"""
Woo Translation Reconcile (Script Report) — data from WooCommerce ONLY.

One row per Arabic (source) product with its English (/en/) WPML twin beside it,
flagging what is not translated:
    Missing English  |  Name not translated  |  Description not translated
    |  Compatibility not translated

The pairing + flags are computed on WooCommerce (it knows the WPML links) and
returned by the plugin route  GET /wp-json/erpgulf/v1/translation-report .
This report just fetches + renders. Read-only. Uses the enabled WooCommerce
Server's own consumer key/secret (same creds woocommerce_fusion syncs with).

Place at:
    <app>/<app>/report/woo_translation_reconcile/
Then `bench --site <site> migrate`.
"""

import frappe
from frappe import _

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

WOO_SERVER_DOCTYPE = "WooCommerce Server"
API_TIMEOUT = 180


def _get_woo_server():
    """(base_url, consumer_key, consumer_secret) from the enabled WooCommerce Server."""
    name = frappe.db.get_value(WOO_SERVER_DOCTYPE, {"enable_sync": 1}, "name")
    if not name:
        frappe.throw(_("No WooCommerce Server has 'Enable Sync' ticked."))
    doc = frappe.get_doc(WOO_SERVER_DOCTYPE, name)
    base = (doc.get("woocommerce_server_url") or "").rstrip("/")
    key = (doc.get("api_consumer_key") or "").strip()
    sec = (doc.get("api_consumer_secret") or "").strip()
    if not (base and key and sec):
        frappe.throw(_("Enabled WooCommerce Server '{0}' is missing URL / key / secret.").format(name))
    return base, key, sec


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"label": _("SKU"),               "fieldname": "sku",       "fieldtype": "Data", "width": 150},
        {"label": _("Issue"),             "fieldname": "issue",     "fieldtype": "Data", "width": 170},
        {"label": _("Arabic name"),       "fieldname": "ar_name",   "fieldtype": "Data", "width": 240},
        {"label": _("English name"),      "fieldname": "en_name",   "fieldtype": "Data", "width": 240},
        {"label": _("Arabic desc"),       "fieldname": "ar_desc",   "fieldtype": "Data", "width": 200},
        {"label": _("English desc"),      "fieldname": "en_desc",   "fieldtype": "Data", "width": 200},
        {"label": _("Compat rows"),       "fieldname": "ar_compat", "fieldtype": "Int",  "width": 90},
        {"label": _("AR id"),             "fieldname": "ar_id",     "fieldtype": "Data", "width": 80},
        {"label": _("EN id"),             "fieldname": "en_id",     "fieldtype": "Data", "width": 80},
        {"label": _("EN compat"), "fieldname": "en_compat", "fieldtype": "Int",  "width": 90},
        {"label": _("Promo"),     "fieldname": "promo",     "fieldtype": "Data", "width": 90},
    ]


def get_data(filters):
    if requests is None:
        frappe.throw(_("The 'requests' library is not available on this bench."))
    base, key, sec = _get_woo_server()

    params = {
        "sku": filters.get("sku") or "",
        "only_issues": 1 if filters.get("only_issues") else 0,
        "limit": int(filters.get("max_products") or 0),
    }
    try:
        resp = requests.get(
            base + "/wp-json/erpgulf/v1/translation-report",
            params=params, auth=(key, sec), timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        frappe.throw(_("Could not fetch translation report from WooCommerce: {0}").format(e))

    payload = resp.json() or {}
    rows = payload.get("rows", []) or []

    issue_filter = filters.get("issue")
    out = []
    for r in rows:
        if issue_filter and issue_filter not in (r.get("issue") or ""):
            continue
        out.append(r)
    return out


# ─────────────────────────────────────────────────────────────────
# Translate action — kick off an AI translate batch on WooCommerce
# for the untranslated products (background + progress).
# ─────────────────────────────────────────────────────────────────

@frappe.whitelist()
def translate_untranslated(sku=None, limit=50):
    if requests is None:
        frappe.throw(_("The 'requests' library is not available on this bench."))
    base, key, sec = _get_woo_server()
    try:
        r = requests.post(
            base + "/wp-json/erpgulf/v1/translate-run",
            params={"sku": sku or "", "limit": int(limit or 50)},
            auth=(key, sec), timeout=60,
        )
    except Exception as e:
        frappe.throw(_("Could not reach WooCommerce: {0}").format(e))
    if r.status_code == 409:
        return {"queued": False, "running": True, "message": "A translate batch is already running."}
    if r.status_code not in (200, 202):
        frappe.throw(_("WooCommerce returned {0}: {1}").format(r.status_code, r.text[:300]))
    try:
        return r.json()
    except Exception:
        return {"queued": True, "running": True, "message": "Translate queued."}


@frappe.whitelist()
def is_translation_running():
    if requests is None:
        return {"running": False, "error": "requests not available"}
    try:
        base, key, sec = _get_woo_server()
        r = requests.get(base + "/wp-json/erpgulf/v1/translate-status", auth=(key, sec), timeout=30)
        r.raise_for_status()
        return r.json() or {}
    except Exception as e:
        return {"running": False, "error": str(e)}