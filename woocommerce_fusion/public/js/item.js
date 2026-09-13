frappe.ui.form.on("Item", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(
            __("Translate this Item in WooCommerce"),
            () => {
                frappe.dom.freeze(__("Queuing translation…"));
                frappe.call({
                    method: "woocommerce_fusion.api.translate.translate_item_in_woocommerce",
                    args: { item_code: frm.doc.name },
                    callback(r) {
                        frappe.dom.unfreeze();
                        if (r.message && r.message.ok) {
                            frappe.msgprint({
                                title: __("Translation queued"),
                                message: __("WooCommerce product {0} will be translated shortly.", [r.message.post_id]),
                                indicator: "green",
                            });
                        }
                    },
                    error() {
                        frappe.dom.unfreeze();
                    },
                });
            },
            __("Actions")
        );
    },
});