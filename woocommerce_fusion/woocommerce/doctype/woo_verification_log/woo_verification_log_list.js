frappe.listview_settings["Woo Verification Log"] = {
    onload: function (listview) {

        listview.page.add_action_item(__('🔄 Sync Selected with WooCommerce'), function () {
            const selected = listview.get_checked_items();
            if (!selected.length) {
                frappe.msgprint(__('Please select at least one record.'));
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

        listview.page.add_action_item(__('🔍 Re-Verify Selected in WooCommerce'), function () {
            const selected = listview.get_checked_items();
            if (!selected.length) {
                frappe.msgprint(__('Please select at least one record.'));
                return;
            }
            frappe.confirm(
                `Re-verify ${selected.length} item(s) against WooCommerce?`,
                function () {
                    let done = 0, matched = 0, mismatched = 0, errors = 0;
                    frappe.show_progress("Verifying...", 0, selected.length, "Please wait");

                    const run_next = (index) => {
                        if (index >= selected.length) {
                            frappe.hide_progress();
                            frappe.msgprint({
                                title: __("Re-Verification Complete"),
                                message: `
                                    <div style="font-size:15px;margin-bottom:12px">
                                        Verified <b>${selected.length}</b> item(s)
                                    </div>
                                    <table style="width:100%;border-collapse:collapse;font-size:14px">
                                        <tr style="background:#e8f5e9">
                                            <td style="padding:8px 16px;font-weight:600">✅ All Fields Match</td>
                                            <td style="padding:8px 16px;font-weight:700;font-size:18px">${matched}</td>
                                        </tr>
                                        <tr style="background:#ffebee">
                                            <td style="padding:8px 16px;font-weight:600">❌ Mismatches Found</td>
                                            <td style="padding:8px 16px;font-weight:700;font-size:18px">${mismatched}</td>
                                        </tr>
                                        <tr style="background:#f5f5f5">
                                            <td style="padding:8px 16px;font-weight:600">⚠️ Errors</td>
                                            <td style="padding:8px 16px;font-weight:700;font-size:18px">${errors}</td>
                                        </tr>
                                    </table>
                                    <div style="margin-top:12px;color:#666;font-size:12px">
                                        Results saved to Woo Verification Log
                                    </div>`,
                                indicator: mismatched ? "red" : "green",
                                wide: true
                            });
                            listview.refresh();
                            return;
                        }
                        frappe.call({
                            method: "woocommerce_fusion.tasks.sync_items.verify_item_woo_match_and_log",
                            args: { item_code: selected[index].item_code },
                            callback(r) {
                                done++;
                                if (r.message) {
                                    r.message.overall ? matched++ : mismatched++;
                                } else {
                                    errors++;
                                }
                                frappe.show_progress("Verifying...", done, selected.length, selected[index].item_code);
                                run_next(index + 1);
                            },
                            error() {
                                done++; errors++;
                                frappe.show_progress("Verifying...", done, selected.length, selected[index].item_code);
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