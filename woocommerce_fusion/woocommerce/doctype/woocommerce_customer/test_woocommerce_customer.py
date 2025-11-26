# # Copyright (c) 2025, Dirk van der Laarse and Contributors
# # See license.txt

# # import frappe
# from frappe.tests.utils import FrappeTestCase


# class TestWoocommerceCustomer(FrappeTestCase):
# 	pass




import frappe
from frappe.tests.utils import FrappeTestCase

class TestWoocommerceCustomer(FrappeTestCase):
    def test_customer_sync_function(self):
        result = frappe.call("woocommerce_fusion.woocommerce.doctype.woocommerce_customer.woocommerce_customer.run_customer_sync", woocommerce_customer_name="demo.mrkbatx.com:1")
        self.assertIn("message", result)
