frappe.ui.form.on("Woo Verification Log", {
    refresh(frm) {
        // Sync to WooCommerce
        frm.add_custom_button(__("🔄 Sync to WooCommerce"), function () {
            frappe.confirm(
                `Sync ${frm.doc.item_code} with WooCommerce?`,
                function () {
                    frappe.call({
                        method: "woocommerce_fusion.tasks.sync_items.run_item_sync",
                        args: { item_code: frm.doc.item_code },
                        freeze: true,
                        freeze_message: __("Syncing with WooCommerce..."),
                        callback: function (r) {
                            frappe.show_alert({
                                message: __("Sync completed successfully"),
                                indicator: "green"
                            }, 5);
                        },
                        error: () => {
                            frappe.show_alert({
                                message: __("Sync failed. See Error Log."),
                                indicator: "red"
                            }, 5);
                        }
                    });
                }
            );
        });

        // Re-verify
        frm.add_custom_button(__("🔍 Re-Verify in WooCommerce"), function () {
            frappe.call({
                method: "woocommerce_fusion.tasks.sync_items.verify_item_woo_match_and_log",
                args: { item_code: frm.doc.item_code },
                freeze: true,
                freeze_message: __("Fetching live data from WooCommerce..."),
                callback(r) {
                    if (!r.message) return;
                    const result = r.message;
                    let rows = "";
                    for (const [field, data] of Object.entries(result.fields)) {
                        const icon = data.match ? "✅" : "❌";
                        const bg = data.match ? "#f1f8f1" : "#fff5f5";
                        const erp_val = `<td style="padding:8px 12px;color:#1a73e8;font-size:13px">${data.erp ?? ""}</td>`;
                        const wc_val = `<td style="padding:8px 12px;color:#e65100;font-size:13px">${data.wc ?? ""}</td>`;
                        rows += `
                            <tr style="background:${bg};border-bottom:1px solid #eee">
                                <td style="padding:8px 12px;font-weight:600;color:#333;min-width:140px">${icon} ${field}</td>
                                ${erp_val}
                                ${wc_val}
                            </tr>`;
                    }
                    const overall_color = result.overall ? "#2e7d32" : "#c62828";
                    const overall_bg = result.overall ? "#e8f5e9" : "#ffebee";
                    const overall_text = result.overall ? "✅ All fields match" : "❌ Mismatches found";
                    frappe.msgprint({
                        title: __("WooCommerce Verification"),
                        message: `
                            <div style="margin-bottom:12px;padding:10px 16px;background:${overall_bg};border-radius:6px;font-weight:600;color:${overall_color};font-size:15px">
                                ${overall_text}
                            </div>
                            <table style="width:100%;border-collapse:collapse;font-size:13px">
                                <thead>
                                    <tr style="background:#f5f5f5">
                                        <th style="padding:8px 12px;text-align:left;color:#555">Field</th>
                                        <th style="padding:8px 12px;text-align:left;color:#1a73e8">ERPNext</th>
                                        <th style="padding:8px 12px;text-align:left;color:#e65100">WooCommerce</th>
                                    </tr>
                                </thead>
                                <tbody>${rows}</tbody>
                            </table>`,
                        indicator: result.overall ? "green" : "red",
                        wide: true
                    });
                    frm.reload_doc();
                }
            });
        });
    }
});