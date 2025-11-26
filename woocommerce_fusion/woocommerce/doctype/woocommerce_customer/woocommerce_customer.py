# Copyright (c) 2025, Dirk van der Laarse and contributors
# For license information, please see license.txt

# import frappe
# from frappe.model.document import Document


# class WoocommerceCustomer(Document):
	
# 	def db_insert(self, *args, **kwargs):
# 		pass

# 	def load_from_db(self):
# 		pass

# 	def db_update(self):
# 		pass

# 	@staticmethod
# 	def get_list(args):
# 		pass

# 	@staticmethod
# 	def get_count(args):
# 		pass

# 	@staticmethod
# 	def get_stats(args):
# 		pass




# apps/woocommerce_fusion/woocommerce_fusion/woocommerce/doctype/woocommerce_customer/woocommerce_customer.py

import frappe
from frappe.model.document import Document
from woocommerce import API
from urllib.parse import unquote


class WoocommerceCustomer(Document):
    """Virtual DocType for WooCommerce Customers"""
    

    @staticmethod
    def _init_api():
        wc_servers = frappe.get_all("WooCommerce Server", filters={"enable_sync": 1})
        wc_servers = [frappe.get_doc("WooCommerce Server", s.name) for s in wc_servers]

        wc_api_list = []
        for server in wc_servers:
            wc_api_list.append(API(
                url=server.woocommerce_server_url,
                consumer_key=server.api_consumer_key,
                consumer_secret=server.get_password("api_consumer_secret"),
                version="wc/v3",
                timeout=30
            ))
        return wc_api_list

    # ---------- Virtual DocType Core ----------
    @staticmethod
    def get_list(args):
        """Fetch list of customers from WooCommerce"""
        wc_api_list = WoocommerceCustomer._init_api()
        records = []

        for wcapi in wc_api_list:
            try:
                customers = wcapi.get("customers").json()
                for c in customers:
                    records.append({
                        "name": f"{wcapi.url}:{c.get('id')}",
                        "woocommerce_server": wcapi.url,
                        "customer_name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                        "email": c.get("email"),
                        "date_created": c.get("date_created") 
                        # "creation": c.get("date_created")
                    })
            except Exception as e:
                frappe.log_error(frappe.get_traceback(), "WooCommerce Customer List Fetch Failed")

        # Apply pagination
        start = int(args.get("limit_start") or 0)
        page_length = int(args.get("limit_page_length") or 20)
        return records[start:start + page_length]

    @staticmethod
    def get_count(args):
        """Return total customer count"""
        wc_api_list = WoocommerceCustomer._init_api()
        count = 0
        for wcapi in wc_api_list:
            try:
                customers = wcapi.get("customers").json()
                count += len(customers)
            except Exception:
                continue
        return count

    def load_from_db(self):
        """Load individual WooCommerce customer when opened"""
        from urllib.parse import unquote
        decoded_name = unquote(self.name)  # Decode URL-encoded string

        # Fix: split only at the last colon
        if ":" not in decoded_name:
            frappe.throw(f"Invalid Woocommerce Customer name: {decoded_name}")

        wc_server_url, customer_id = decoded_name.rsplit(":", 1)

        # Initialize internals for Virtual Doctype
        self._table_fieldnames = []
        self.flags = frappe._dict()

        # Fetch WooCommerce Server
        wc_server = frappe.get_doc("WooCommerce Server", {"woocommerce_server_url": wc_server_url})
        wcapi = API(
            url=wc_server.woocommerce_server_url,
            consumer_key=wc_server.api_consumer_key,
            consumer_secret=wc_server.get_password("api_consumer_secret"),
            version="wc/v3"
        )

        # Fetch customer data
        data = wcapi.get(f"customers/{customer_id}").json()

        # Populate Frappe document fields
        self.woocommerce_server=wc_server.name
        self.customer_name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        self.email = data.get("email")
        self.date_created = data.get("date_created")


        billing = data.get("billing", {}) or {}
        frappe.log_error("billing1",billing)
        # self.phone = billing.get("phone")
        # self.address = billing.get("address_1")
        # self.city = billing.get("city")



    def db_insert(self):
        frappe.throw("Cannot insert manually: this is a virtual document.")

    def db_update(self):
        frappe.throw("Cannot update manually: this is a virtual document.")

    def delete(self):
        frappe.throw("Cannot delete manually: this is a virtual document.")




@frappe.whitelist()
def run_customer_sync(woocommerce_customer_name):
    """Sync selected WooCommerce Customer into ERPNext Customer doctype."""
    # frappe.log_error("1")
    wc_server_url, customer_id = woocommerce_customer_name.rsplit(":", 1)
    wc_server = frappe.get_doc("WooCommerce Server", {"woocommerce_server_url": wc_server_url})
    wcapi = API(
        url=wc_server.woocommerce_server_url,
        consumer_key=wc_server.api_consumer_key,
        consumer_secret=wc_server.get_password("api_consumer_secret"),
        version="wc/v3"
    )

    data = wcapi.get(f"customers/{customer_id}").json()
    email = data.get("email")
    if not email:
        frappe.throw("Customer email missing")

    existing = frappe.db.exists("Customer", {"email_id": email})
    if existing:
        # frappe.log_error("2")
        # frappe.throw("Customer already exists")
        return {"message": "Customer already exists", "name": existing}

    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or email,
        "customer_group": "All Customer Groups",
        "territory": "All Territories",
        "customer_type": "Individual",
        "email_id": email,
        "mobile_no": data.get("billing", {}).get("phone")
    })
    customer.insert(ignore_permissions=True) 
    
    # Create Address if available
    billing = data.get("billing", {}) or {}
    frappe.log_error("billing",billing)
    country_code = billing.get("country")
    country_name = frappe.db.get_value("Country", {"code": country_code}, "name") or country_code
    if billing.get("address_1"):
        address = frappe.get_doc({
            "doctype": "Address",
            "address_title": customer.customer_name,
            "address_type": "Billing",
            "address_line1": billing.get("address_1"),
            "address_line2": billing.get("address_2"),
            "city": billing.get("city"),
            "state": billing.get("state"),
            "pincode": billing.get("postcode"),
            # "country": billing.get("country"),
            "country": country_name,
            "phone": billing.get("phone"),
            "email_id": email
        })
        address.insert(ignore_permissions=True)
        address.append("links", {
            "link_doctype": "Customer",
            "link_name": customer.name
        })
        address.save(ignore_permissions=True)
    frappe.db.commit()

    return {"message": "Customer created successfully", "name": customer.name}
