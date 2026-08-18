// Pricing Rule list view — adds a "Sync Promotion to Woo" button.
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

        // Always-visible toolbar button (top of the list, next to List View / Actions)
        listview.page.add_inner_button(__('Sync Promotion to Woo'), function () {
            const items = listview.get_checked_items() || [];
            const names = items.map(function (d) { return d.name; });
            if (!names.length) {
                frappe.msgprint(__('Tick one or more Pricing Rules first, then click Sync Promotion to Woo.'));
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