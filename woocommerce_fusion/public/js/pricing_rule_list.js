// Pricing Rule list view — adds "Sync Promotion to Woo" to the Actions menu.
// Ships with the app (no manual Client Script needed).
// Register in hooks.py:  doctype_list_js = {"Pricing Rule": "public/js/pricing_rule_list.js"}
// Place at:  woocommerce_fusion/public/js/pricing_rule_list.js

frappe.listview_settings['Pricing Rule'] = frappe.listview_settings['Pricing Rule'] || {};

(function () {
    const prev_onload = frappe.listview_settings['Pricing Rule'].onload;

    frappe.listview_settings['Pricing Rule'].onload = function (listview) {
        // keep any existing onload (ERPNext / other apps) intact
        if (prev_onload) {
            try { prev_onload(listview); } catch (e) { /* noop */ }
        }

        listview.page.add_action_item(__('Sync Promotion to Woo'), function () {
            const names = listview.get_checked_items(true);   // selected docnames only
            if (!names || !names.length) {
                frappe.msgprint(__('Select one or more Pricing Rules first.'));
                return;
            }
            frappe.call({
                method: 'woocommerce_fusion.tasks.price_tiers.sync_pricing_rules_to_woo',
                args: { names: names },
                freeze: true,
                freeze_message: __('Pushing promo tiers to WooCommerce…'),
                callback: function (r) {
                    const d = (r && r.message) || {};
                    frappe.msgprint(
                        __('Promo pushed to WooCommerce. SKUs sent: {0}, rows updated: {1}.<br>Items: {2}',
                            [d.sent || 0, d.updated || 0, (d.items || []).join(', ') || '—'])
                    );
                }
            });
        });
    };
})();