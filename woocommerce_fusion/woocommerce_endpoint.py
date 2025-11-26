import base64
import hashlib
import hmac
import json
from http import HTTPStatus
from typing import Optional, Tuple

import frappe
from frappe import _
from werkzeug.wrappers import Response

from woocommerce_fusion.tasks.sync_sales_orders import run_sales_order_sync
from woocommerce_fusion.woocommerce.woocommerce_api import (
	WC_RESOURCE_DELIMITER,
	parse_domain_from_url,
)


def validate_request() -> Tuple[bool, Optional[HTTPStatus], Optional[str]]:
	# Get relevant WooCommerce Server
	try:
		webhook_source_url = frappe.get_request_header("x-wc-webhook-source", "")
		wc_server = frappe.get_doc("WooCommerce Server", parse_domain_from_url(webhook_source_url))
	except Exception:
		return False, HTTPStatus.BAD_REQUEST, _("Missing Header")

	# Validate secret
	sig = base64.b64encode(
		hmac.new(wc_server.secret.encode("utf8"), frappe.request.data, hashlib.sha256).digest()
	)
	# if (
	# 	frappe.request.data
	# 	and not sig == frappe.get_request_header("x-wc-webhook-signature", "").encode()
	# ):
	# 	return False, HTTPStatus.UNAUTHORIZED, _("Unauthorized")

	frappe.set_user(wc_server.creation_user)
	return True, None, None


@frappe.whitelist(allow_guest=True, methods=["POST"])
def order_created(*args, **kwargs):
    
	"""
	Accepts payload data from WooCommerce "Order Created" webhook
	"""
    
	valid, status, msg = validate_request()
	if not valid:
		return Response(response=msg, status=status)

	if frappe.request and frappe.request.data:
		try:
			order = json.loads(frappe.request.data)
		except ValueError:
			# woocommerce returns 'webhook_id=value' for the first request which is not JSON
			order = frappe.request.data
		event = frappe.get_request_header("x-wc-webhook-event")
	else:
		return Response(response=_("Missing Header"), status=HTTPStatus.BAD_REQUEST)

	if event == "created":
		webhook_source_url = frappe.get_request_header("x-wc-webhook-source", "")
		woocommerce_order_name = (
			f"{parse_domain_from_url(webhook_source_url)}{WC_RESOURCE_DELIMITER}{order['id']}"
		)
		frappe.enqueue(run_sales_order_sync, queue="long", woocommerce_order_name=woocommerce_order_name)
		return Response(status=HTTPStatus.OK)
	else:
		return Response(response=_("Event not supported"), status=HTTPStatus.BAD_REQUEST)



@frappe.whitelist(allow_guest=True, methods=["POST"])
def customer_created(*args, **kwargs):
	"""
	Accepts payload data from WooCommerce "Customer Created" webhook
	and creates a Customer in ERPNext automatically.
	"""
    # frappe.log_error("customer creation")
	valid, status, msg = validate_request()
	if not valid:
		return Response(response=msg, status=status)

	if frappe.request and frappe.request.data:
		try:
			data = json.loads(frappe.request.data)
		except ValueError:
			data = frappe.request.data
		event = frappe.get_request_header("x-wc-webhook-event")
	else:
		return Response(response=_("Missing Header"), status=HTTPStatus.BAD_REQUEST)

	if event != "created":
		return Response(response=_("Event not supported"), status=HTTPStatus.BAD_REQUEST)

	# --- Extract WooCommerce customer details ---
	full_name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
	email = data.get("email") or data.get("billing", {}).get("email")
	phone = data.get("billing", {}).get("phone")

	if not email:
		return Response(response=_("Customer email missing"), status=HTTPStatus.BAD_REQUEST)

	# --- Check if customer already exists ---
	existing = frappe.db.exists("Customer", {"email_id": email})
	if existing:
		return Response(
			response=json.dumps({"message": "Customer already exists", "name": existing}),
			status=HTTPStatus.OK,
			content_type="application/json",
		)

	# --- Create new Customer ---
	customer = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": full_name or email,
		"customer_group": "All Customer Groups",
		"territory": "All Territories",
		"customer_type": "Individual",
		"email_id": email,
		"mobile_no": phone
	})
	customer.insert(ignore_permissions=True)
	frappe.db.commit()

	return Response(
		response=json.dumps({"message": "Customer created", "name": customer.name}),
		status=HTTPStatus.OK,
		content_type="application/json",
	)
 
@frappe.whitelist()
def sync_customers_from_woocommerce():
    """Sync all customers from WooCommerce to ERPNext manually."""
    from woocommerce import API

    try:
        wc_server = frappe.get_doc("WooCommerce Server", "demo.mrkbatx.com")
    except frappe.DoesNotExistError:
        frappe.throw("WooCommerce Server 'demo.mrkbatx.com' not found")

    wcapi = API(
        url=wc_server.woocommerce_server_url,
        consumer_key=wc_server.api_consumer_key,
        consumer_secret=wc_server.get_password("api_consumer_secret"),
        version="wc/v3"
    )

    customers = wcapi.get("customers").json()

    created_count = 0
    skipped_count = 0
    failed_customers = []

    for data in customers:
        try:
            email = data.get("email")
            first_name = data.get("first_name", "").strip()
            last_name = data.get("last_name", "").strip()
            phone = data.get("billing", {}).get("phone")

            # Skip invalid or empty emails
            if not email:
                skipped_count += 1
                continue

            # Skip if already exists
            if frappe.db.exists("Customer", {"email_id": email}):
                skipped_count += 1
                continue

            # Create customer
            customer = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": f"{first_name} {last_name}".strip() or email,
                "customer_group": "All Customer Groups",
                "territory": "All Territories",
                "customer_type": "Individual",
                "email_id": email,
                "mobile_no": phone
            })

            customer.insert(ignore_permissions=True)
            frappe.db.commit()
            created_count += 1

        except Exception as e:
            failed_customers.append({
                "email": data.get("email"),
                "error": str(e)
            })
            frappe.log_error(message=frappe.get_traceback(), title="WooCommerce Customer Sync Failed")

    result = {
        "created": created_count,
        "skipped": skipped_count,
        "failed": len(failed_customers),
        "failed_customers": failed_customers,
    }

    return result
