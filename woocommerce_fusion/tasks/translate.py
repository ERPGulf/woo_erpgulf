import time

import frappe
import requests


def _server_auth(server):
	return {
		"consumer_key": server.api_consumer_key,
		"consumer_secret": server.get_password("api_consumer_secret"),
	}


def _get_server():
	names = frappe.get_all("WooCommerce Server", filters={"enable_sync": 1}, pluck="name", limit=1)
	if not names:
		frappe.throw("No enabled WooCommerce Server found")
	return frappe.get_doc("WooCommerce Server", names[0])


def _fetch_status(server, post_id):
	base = server.woocommerce_server_url.rstrip("/")
	params = _server_auth(server)
	params["post_id"] = post_id
	try:
		r = requests.get(f"{base}/wp-json/erpgulf-gt/v1/translate-status", params=params, timeout=30)
		if r.status_code >= 400:
			return None
		return r.json()
	except requests.RequestException:
		return None


def _write_log(item_code, post_id, server_name, status, duration, summary="", skip_reason="", error_detail=""):
	try:
		frappe.get_doc({
			"doctype": "Woo Sync Log",
			"item_code": item_code,
			"item_name": frappe.db.get_value("Item", item_code, "item_name"),
			"woocommerce_id": str(post_id or ""),
			"woocommerce_server": server_name or "",
			"sync_date": frappe.utils.nowdate(),
			"sync_time": frappe.utils.nowtime(),
			"duration_seconds": round(duration or 0, 2),
			"sync_trigger": "Manual",
			"status": status,
			"response_summary": summary,
			"skip_reason": skip_reason,
			"error_detail": error_detail,
		}).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception as exc:
		frappe.log_error(f"Woo Sync Log (translate) write failed: {exc}")


@frappe.whitelist()
def translate_item_in_woocommerce(item_code: str, force: int = 1) -> dict:
	if not item_code:
		frappe.throw("item_code is required")

	frappe.has_permission("Item", ptype="write", doc=item_code, throw=True)

	server = _get_server()
	base = server.woocommerce_server_url.rstrip("/")

	try:
		response = requests.post(
			f"{base}/wp-json/erpgulf-gt/v1/translate",
			params=_server_auth(server),
			data={"sku": item_code, "force": int(force)},
			timeout=30,
		)
	except requests.RequestException as exc:
		frappe.log_error(frappe.get_traceback(), "WooCommerce translate request failed")
		_write_log(item_code, "", server.name, "Failed", 0, error_detail=str(exc))
		frappe.throw(f"Could not reach WooCommerce: {exc}")

	if response.status_code >= 400:
		_write_log(item_code, "", server.name, "Failed", 0,
			error_detail=f"HTTP {response.status_code}: {response.text[:500]}")
		frappe.throw(f"WooCommerce returned {response.status_code}: {response.text[:200]}")

	data = response.json()
	post_id = data.get("post_id")

	before = _fetch_status(server, post_id) or {}

	frappe.enqueue(
		"woocommerce_fusion.tasks.translate.poll_translation",
		queue="long",
		timeout=900,
		item_code=item_code,
		post_id=post_id,
		server_name=server.name,
		before_time=before.get("time") or "",
		started_at=time.time(),
	)

	return data


def poll_translation(item_code, post_id, server_name, before_time, started_at, attempts=20, interval=15):
	server = frappe.get_doc("WooCommerce Server", server_name)

	for _ in range(attempts):
		time.sleep(interval)
		data = _fetch_status(server, post_id)
		if not data:
			continue
		stamp = data.get("time") or ""
		if not stamp or stamp == before_time:
			continue

		wp_status = (data.get("status") or "").lower()
		detail = data.get("detail") or ""
		duration = time.time() - started_at
		summary = "WordPress: {0}\nEnglish product: {1} (#{2})".format(
			wp_status or "unknown", data.get("en_title") or "-", data.get("en_post_id") or 0
		)

		if wp_status == "ok":
			_write_log(item_code, post_id, server_name, "Success", duration, summary=summary)
		elif wp_status == "skip":
			_write_log(item_code, post_id, server_name, "Skipped", duration,
				summary=summary, skip_reason=detail)
		else:
			_write_log(item_code, post_id, server_name, "Failed", duration,
				summary=summary, error_detail=detail)
		return

	_write_log(item_code, post_id, server_name, "Failed", time.time() - started_at,
		error_detail="No result from WooCommerce within {0} seconds".format(attempts * interval))