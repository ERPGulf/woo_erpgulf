frappe.listview_settings["Woo Sync Log"] = {
    onload(listview) {

        // Sync selected items
        listview.page.add_action_item("🔄 Sync Selected with WooCommerce", function () {
            const selected = listview.get_checked_items();
            if (!selected.length) {
                frappe.msgprint("Please select at least one record.");
                return;
            }
            frappe.confirm(
                `Sync ${selected.length} item(s) with WooCommerce?`,
                function () {
                    frappe.call({
                        method: "woocommerce_fusion.tasks.sync_items.bulk_run_item_sync",
                        args: { items: selected.map(d => d.item_code) },
                        callback: function (r) {
                            if (r.message) {
                                frappe.show_alert({
                                    message: r.message.message,
                                    indicator: "blue"
                                }, 5);
                            }
                        }
                    });
                }
            );
        });

        // Verify selected
        listview.page.add_action_item("🔍 Verify Selected in WooCommerce", function () {
            const selected = listview.get_checked_items();
            if (!selected.length) {
                frappe.msgprint("Please select at least one record.");
                return;
            }
            frappe.confirm(
                `Verify ${selected.length} record(s) against WooCommerce?`,
                function () {
                    let done = 0;
                    let failed = 0;
                    frappe.show_progress("Verifying...", 0, selected.length);

                    const run_next = (index) => {
                        if (index >= selected.length) {
                            frappe.hide_progress();
                            frappe.msgprint({
                                title: "Verification Complete",
                                message: `✅ Matched: ${done - failed}<br>❌ Mismatched: ${failed}<br>Total: ${selected.length}`,
                                indicator: failed ? "red" : "green"
                            });
                            listview.refresh();
                            return;
                        }
                        frappe.call({
                            method: "woocommerce_fusion.tasks.sync_items.verify_woo_match",
                            args: { log_name: selected[index].name },
                            callback(r) {
                                done++;
                                if (r.message && !r.message.overall) failed++;
                                frappe.show_progress("Verifying...", done, selected.length);
                                run_next(index + 1);
                            },
                            error() {
                                done++;
                                failed++;
                                frappe.show_progress("Verifying...", done, selected.length);
                                run_next(index + 1);
                            }
                        });
                    };
                    run_next(0);
                }
            );
        });
    }
};