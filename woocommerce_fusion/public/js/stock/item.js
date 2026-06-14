frappe.ui.form.on('Item', {
	refresh: function (frm) {
		// Add a custom button to sync Item Stock with WooCommerce
		frm.add_custom_button(__("Sync this Item's Stock Levels to WooCommerce"), function () {
			frm.trigger("sync_item_stock");
		}, __('Actions'));

		// Add a custom button to sync Item Price with WooCommerce
		frm.add_custom_button(__("Sync this Item's Price to WooCommerce"), function () {
			frm.trigger("sync_item_price");
		}, __('Actions'));

		// Add a custom button to sync Item with WooCommerce
		frm.add_custom_button(__("Sync this Item with WooCommerce"), function () {
			frm.trigger("sync_item");
		}, __('Actions'));

		// Add verify button
		if (frm.doc.woocommerce_servers && frm.doc.woocommerce_servers.length) {
			frm.add_custom_button(__("🔍 Verify in WooCommerce"), function () {
				frm.trigger("verify_woo");
			}, __('Actions'));
		}
	},

	verify_woo: function (frm) {
		frappe.call({
			method: "woocommerce_fusion.tasks.sync_items.verify_item_woo_match",
			args: { item_code: frm.doc.name },
			freeze: true,
			freeze_message: __("Fetching live data from WooCommerce..."),
			callback(r) {
				if (!r.message) return;
				const result = r.message;
				let rows = "";
				for (const [field, data] of Object.entries(result.fields)) {
					const icon = data.match ? "✅" : "❌";
					const bg = data.match ? "#f1f8f1" : "#fff5f5";
					const erp_val = data.erp !== undefined
						? `<td style="padding:8px 12px;color:#1a73e8;font-size:13px">${data.erp}</td>`
						: `<td></td>`;
					const wc_val = data.wc !== undefined
						? `<td style="padding:8px 12px;color:#e65100;font-size:13px">${data.wc}</td>`
						: `<td></td>`;
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
			}
		});
	},

	sync_item_stock: function (frm) {
		frappe.dom.freeze(__("Sync Item Stock with WooCommerce..."));
		frappe.call({
			method: "woocommerce_fusion.tasks.stock_update.update_stock_levels_on_woocommerce_site",
			args: { item_code: frm.doc.name },
			callback: function (r) {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('Synchronised stock level to WooCommerce for enabled servers'),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			},
			error: (r) => {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('There was an error processing the request. See Error Log.'),
					indicator: 'red'
				}, 5);
			}
		});
	},

	sync_item_price: function (frm) {
		frappe.dom.freeze(__("Sync Item Price with WooCommerce..."));
		frappe.call({
			method: "woocommerce_fusion.tasks.sync_item_prices.run_item_price_sync",
			args: { item_code: frm.doc.name },
			callback: function (r) {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('Synchronised item price to WooCommerce'),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			},
			error: (r) => {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('There was an error processing the request. See Error Log.'),
					indicator: 'red'
				}, 5);
			}
		});
	},

	sync_item: function (frm) {
		frappe.dom.freeze(__("Sync Item with WooCommerce..."));
		frappe.call({
			method: "woocommerce_fusion.tasks.sync_items.run_item_sync",
			args: { item_code: frm.doc.name },
			callback: function (r) {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('Sync completed successfully'),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			},
			error: (r) => {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('There was an error processing the request. See Error Log.'),
					indicator: 'red'
				}, 5);
			}
		});
	},
})

frappe.ui.form.on('Item WooCommerce Server', {
	view_product: function (frm, cdt, cdn) {
		let current_row_doc = locals[cdt][cdn];
		console.log(current_row_doc);
		frappe.set_route("Form", "WooCommerce Product", `${current_row_doc.woocommerce_server}~${current_row_doc.woocommerce_id}`);
	}
})