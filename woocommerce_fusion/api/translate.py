import frappe
import requests


@frappe.whitelist()
def translate_item_in_woocommerce(item_code: str) -> dict:
	if not item_code:
		frappe.throw("item_code is required")

	frappe.has_permission("Item", ptype="write", doc=item_code, throw=True)

	server_name = frappe.get_all("WooCommerce Server", filters={"enable_sync": 1}, pluck="name", limit=1)
	if not server_name:
		frappe.throw("No enabled WooCommerce Server found")

	server = frappe.get_doc("WooCommerce Server", server_name[0])

	try:
		response = requests.post(
			f"{server.woocommerce_server_url.rstrip('/')}/wp-json/erpgulf-gt/v1/translate",
			params={
				"consumer_key": server.api_consumer_key,
				"consumer_secret": server.get_password("api_consumer_secret"),
			},
			data={"sku": item_code},
			timeout=30,
		)
	except requests.RequestException as exc:
		frappe.log_error(frappe.get_traceback(), "WooCommerce translate request failed")
		frappe.throw(f"Could not reach WooCommerce: {exc}")

	if response.status_code >= 400:
		frappe.log_error(response.text, "WooCommerce translate error")
		frappe.throw(f"WooCommerce returned {response.status_code}: {response.text[:200]}")

	return response.json()