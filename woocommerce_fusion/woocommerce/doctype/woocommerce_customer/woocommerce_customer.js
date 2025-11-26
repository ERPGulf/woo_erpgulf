// Copyright (c) 2025, Dirk van der Laarse and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Woocommerce Customer", {
// 	refresh(frm) {

// 	},
// });



// apps/woocommerce_fusion/woocommerce_fusion/woocommerce/doctype/woocommerce_customer/woocommerce_customer.js

frappe.ui.form.on("Woocommerce Customer", {
    refresh: function(frm) {
        frm.add_custom_button(__("Sync this Customer to ERPNext"), function() {
            frappe.dom.freeze(__("Syncing Customer..."));
            frappe.call({
                method: "woocommerce_fusion.woocommerce.doctype.woocommerce_customer.woocommerce_customer.run_customer_sync",
                args: {
                    woocommerce_customer_name: frm.doc.name
                },
                callback: function(r) {
                    frappe.dom.unfreeze();
                    if (!r.exc) {
                        frappe.show_alert({
                            message: __("Customer synced successfully"),
                            indicator: "green"
                        }, 5);
                        console.log(r.message);
                    } else {
                        frappe.show_alert({
                            message: __("Error syncing customer"),
                            indicator: "red"
                        }, 5);
                    }
                }
            });
        }, __("Actions"));

        frm.set_intro(
            __("Note: This is a Virtual Document. Saving changes will update the resource on WooCommerce."),
            "orange"
        );
    }
});

