"""
Automatic ERPNext -> WooCommerce push for stock & price changes.

Fires the same per-item sync the "Sync shown to Woo" button ultimately runs
(woocommerce_fusion.tasks.sync_items.run_item_sync), but automatically,
whenever a product's price or stock changes in ERPNext.

Triggers (wired via hooks.py doc_events):
  - Item Price  create / update / delete  -> price changed  -> push that item
  - Stock Ledger Entry  insert            -> stock moved    -> push that item

Design:
  - Debounced per item via redis cache: a burst of stock movements or price
    edits for one item collapses into a SINGLE background push (no API hammering,
    never blocks the saving transaction).
  - enqueue_after_commit: only pushes once the DB change is actually committed.
    - Reuses run_item_sync, the same worker the manual button drives per item.
  - Kill switch: set  woo_autopush_disabled: 1  in site_config.json to turn off
    without removing code.

Place at:  woocommerce_fusion/tasks/auto_push.py
Then add the doc_events block (see bottom of this file) to hooks.py and migrate.
"""

import time

import frappe

DEBOUNCE_SEC = 90          # collapse repeated changes for one item into one push
QUEUE = "long"             # background queue to run the push on
MAX_ATTEMPTS = 3           # retry a failed push instead of dropping the item
RETRY_DELAY_SEC = 5        # base back-off between attempts (5s, then 10s)


def _enabled():
    return not frappe.conf.get("woo_autopush_disabled")


def _store_price_list():
    """The selling price list the store actually syncs (from the enabled
    WooCommerce Server), cached per request."""
    pl = getattr(frappe.local, "_woo_autopush_pl", None)
    if pl is not None:
        return pl
    name = frappe.db.get_value("WooCommerce Server", {"enable_sync": 1}, "name")
    pl = (frappe.db.get_value("WooCommerce Server", name, "price_list") if name else None) or ""
    frappe.local._woo_autopush_pl = pl
    return pl


def _eligible(item_code):
    """Only push items that are actually synced to Woo and not blocked — mirrors
    the report's sync_reason() rules. Keeps raw materials / non-web items out."""
    if not item_code:
        return False
    it = frappe.db.get_value(
        "Item", item_code, ["disabled", "custom_disable_sync"], as_dict=True
    )
    if not it or it.get("disabled") or it.get("custom_disable_sync"):
        return False
    # Must be linked to a WooCommerce Server (i.e. a product the sync manages).
    if not frappe.db.exists("Item WooCommerce Server", {"parent": item_code}):
        return False
    return True


def _queue_push(item_code):
    """Enqueue one debounced push for this item code (SKU)."""
    if not item_code or not _enabled():
        return
    if not _eligible(item_code):
        return
    key = "woo_autopush:" + item_code
    # If a push for this item is already pending, skip — the pending job will
    # read the latest stock/price when it runs.
    if frappe.cache().get_value(key):
        return
    frappe.cache().set_value(key, "1", expires_in_sec=DEBOUNCE_SEC)
    frappe.enqueue(
        "woocommerce_fusion.tasks.auto_push.run_push",
        queue=QUEUE,
        job_name="woo_autopush_" + item_code,
        enqueue_after_commit=True,
        item_code=item_code,
    )


def run_push(item_code):
    """Background worker: push one item to Woo via the single-item sync.

    Uses run_item_sync (not bulk_run_item_sync) — the bulk entry point writes
    shared batch-progress rows to tabDefaultValue keyed on the session user,
    so concurrent auto-pushes deadlocked each other there and the item was
    silently dropped. Retries on transient failures instead of giving up.
    """
    frappe.cache().delete_value("woo_autopush:" + item_code)
    if not _enabled():
        return

    from woocommerce_fusion.tasks.sync_items import run_item_sync

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            run_item_sync(item_code=item_code, enqueue=False)
            return
        except Exception:
            last_error = frappe.get_traceback()
            frappe.db.rollback()
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SEC * attempt)

    frappe.log_error(
        last_error,
        "woo_autopush run_push %s (failed after %s attempts)" % (item_code, MAX_ATTEMPTS),
    )


# ─────────────────────────────────────────────────────────────────
# doc_event handlers
# ─────────────────────────────────────────────────────────────────

def on_item_price(doc, method=None):
    """Item Price create/update/delete -> push the item.
    Only the store's own selling price list matters."""
    try:
        if not getattr(doc, "selling", 0):
            return  # ignore buying/cost price lists
        store_pl = _store_price_list()
        if store_pl and getattr(doc, "price_list", None) != store_pl:
            return  # a different selling price list — not the one Woo uses
        _queue_push(getattr(doc, "item_code", None))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "woo_autopush on_item_price")


def on_stock_ledger_entry(doc, method=None):
    """Stock Ledger Entry insert -> stock moved -> push the item."""
    try:
        _queue_push(getattr(doc, "item_code", None))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "woo_autopush on_sle")