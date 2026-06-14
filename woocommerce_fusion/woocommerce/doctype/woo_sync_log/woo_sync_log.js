frappe.ui.form.on("Woo Sync Log", {
    refresh(frm) {
        frm.add_custom_button("🔍 Verify Live in WooCommerce", function () {
            frappe.call({
                method: "woocommerce_fusion.tasks.sync_items.verify_woo_match",
                args: { log_name: frm.doc.name },
                freeze: true,
                freeze_message: "Fetching live data from WooCommerce...",
                callback(r) {
                    if (!r.message) return;
                    const result = r.message;
                    let rows = "";
                    for (const [field, data] of Object.entries(result.fields)) {
                        const icon = data.match ? "✅" : "❌";
                        const color = data.match ? "#2e7d32" : "#c62828";
                        rows += `
                            <tr style="border-bottom:1px solid #f0f0f0">
                                <td style="padding:8px 16px;font-weight:500;color:#333">${field}</td>
                                <td style="padding:8px 16px;text-align:center;font-size:20px;color:${color}">${icon}</td>
                            </tr>`;
                    }
                    const overall_color = result.overall ? "#2e7d32" : "#c62828";
                    const overall_text = result.overall ? "✅ All fields match" : "❌ Mismatches found";
                    frappe.msgprint({
                        title: "WooCommerce Verification Result",
                        message: `
                            <div style="margin-bottom:12px;padding:10px 16px;background:${result.overall ? '#e8f5e9' : '#ffebee'};border-radius:6px;font-weight:600;color:${overall_color};font-size:15px">
                                ${overall_text}
                            </div>
                            <table style="width:100%;border-collapse:collapse;font-size:14px">
                                <thead>
                                    <tr style="background:#f5f5f5">
                                        <th style="padding:8px 16px;text-align:left;color:#555">Field</th>
                                        <th style="padding:8px 16px;text-align:center;color:#555">Status</th>
                                    </tr>
                                </thead>
                                <tbody>${rows}</tbody>
                            </table>`,
                        indicator: result.overall ? "green" : "red"
                    });
                    frm.reload_doc();
                }
            });
        }, "Verify");
    }
});