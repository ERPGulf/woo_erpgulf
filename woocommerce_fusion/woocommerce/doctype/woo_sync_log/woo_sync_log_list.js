frappe.listview_settings["Woo Sync Log"] = {
    onload(listview) {
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