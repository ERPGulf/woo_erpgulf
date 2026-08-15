// Filters for the "Woo Product Reconcile" script report.
frappe.query_reports["Woo Product Reconcile"] = {
    filters: [
        {
            fieldname: "brand",
            label: __("Brand"),
            fieldtype: "Link",
            options: "Brand",
        },
        {
            fieldname: "item_group",
            label: __("Item Group"),
            fieldtype: "Link",
            options: "Item Group",
        },
        {
            fieldname: "sku",
            label: __("SKU contains"),
            fieldtype: "Data",
        },
        {
            fieldname: "warehouse",
            label: __("Warehouse"),
            fieldtype: "Link",
            options: "Warehouse",
            // leave empty to sum stock across all warehouses
        },
        {
            fieldname: "max_products",
            label: __("Max products (blank = all)"),
            fieldtype: "Int",
        },
        {
            fieldname: "only_mismatches",
            label: __("Only mismatches"),
            fieldtype: "Check",
            default: 1,
        },
        {
            fieldname: "include_disabled",
            label: __("Include disabled items"),
            fieldtype: "Check",
            default: 0,
        },
        {
            fieldname: "f_disable_sync",
            label: __("Disable Sync"),
            fieldtype: "Select",
            options: "\nYes\nNo",
        },
        {
            fieldname: "f_vin_required",
            label: __("VIN Required"),
            fieldtype: "Select",
            options: "\nYes\nNo",
        },
        {
            fieldname: "f_disable_sync_no_stock",
            label: __("Disable sync if not in stock"),
            fieldtype: "Select",
            options: "\nYes\nNo",
        },
        {
            fieldname: "f_verified",
            label: __("Verified"),
            fieldtype: "Select",
            options: "\nYes\nNo",
            default: "Yes"
        },
        {
            fieldname: "f_universal",
            label: __("Universal"),
            fieldtype: "Select",
            options: "\nYes\nNo",
        },
    ],

    onload: function (report) {
        const defaultLabel = __("Sync mismatched to Woo");
        const $btn = report.page.add_inner_button(defaultLabel, function () {
            const rows = frappe.query_report.data || [];
            const skus = rows.filter(r => r.can_sync).map(r => r.sku);
            if (!skus.length) {
                frappe.msgprint(__("No mismatched items to sync (run/rebuild the report first)."));
                return;
            }
            frappe.confirm(
                __("Push {0} mismatched item(s) from ERPNext to WooCommerce? This writes to the LIVE store.", [skus.length]),
                function () {
                    frappe.call({
                        method: "woocommerce_fusion.tasks.sync_items.bulk_run_item_sync",
                        args: { items: skus },
                        freeze: true,
                        freeze_message: __("Queuing background sync…"),
                        callback: function (r) {
                            $btn.text(__("Started Sync…")).prop("disabled", true).addClass("disabled");
                            frappe.show_alert({
                                message: __("Sync started for {0} item(s) — running in the background.", [skus.length]),
                                indicator: "green",
                            }, 7);
                        },
                    });
                }
            );
        });
        $btn.addClass("btn-primary");

        // ---- Rebuild Vehicle Fitment (triggers WooCommerce to rebuild fitment + vehicles.csv) ----
        const RECON = "woocommerce_fusion.woocommerce.report.woo_product_reconcile.woo_product_reconcile";
        report.page.add_inner_button(__("Rebuild Vehicle Fitment"), function () {
            frappe.confirm(
                __("Rebuild the fitment index and regenerate vehicles.csv on the store? Runs in the background."),
                function () {
                    frappe.call({
                        method: RECON + ".rebuild_woo_fitments",
                        args: { csv: 1, lookup: 1 },
                        freeze: true,
                        freeze_message: __("Starting fitment rebuild…"),
                        callback: function (r) {
                            const started = (r && r.message) || {};
                            if (started.queued === false && started.running === false) {
                                frappe.msgprint(started.message || __("Could not start rebuild."));
                                return;
                            }
                            const TITLE = __("Vehicle Fitment Rebuild");
                            frappe.show_progress(TITLE, 1, 100, __("Starting…"));
                            let done = false;
                            const t = setInterval(function () {
                                frappe.call({
                                    method: RECON + ".is_fitment_rebuild_running",
                                    callback: function (rr) {
                                        const st = (rr && rr.message) || {};
                                        if (st.running) {
                                            frappe.show_progress(TITLE, st.percent || 1, 100, st.phase || __("Working…"));
                                            return;
                                        }
                                        if (done) return;
                                        done = true;
                                        clearInterval(t);
                                        frappe.show_progress(TITLE, 100, 100, __("Done"));
                                        setTimeout(frappe.hide_progress, 800);
                                        const res = (st.result && st.result.message) || __("done");
                                        const ok = !st.result || st.result.success !== false;
                                        frappe.msgprint({
                                            title: __("Fitment rebuild finished"),
                                            indicator: ok ? "green" : "red",
                                            message: res,
                                        });
                                    },
                                });
                            }, 3000);
                        },
                    });
                }
            );
        });

        frappe.realtime.on("wc_bulk_sync_complete", function () {
            $btn.text(defaultLabel).prop("disabled", false).removeClass("disabled");
            frappe.show_alert({ message: __("WooCommerce sync finished."), indicator: "green" }, 7);
        });
    },
    // Everything from Woo is shown in blue; diffs are highlighted.
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        // 1) All Woo-related columns -> blue
        const wooCols = ["woo_name", "woo_compat", "woo_stock", "woo_price", "woo_status", "woo_id", "woo_matches"];
        if (wooCols.includes(column.fieldname) || (column.fieldname && column.fieldname.indexOf("woowh_") === 0)) {
            value = `<span style="color:#0b5cff">${value}</span>`;
        }

        // 2) Match ticks -> green/red
        if (["stock_match", "price_match"].includes(column.fieldname)) {
            if (value.indexOf("✗") > -1) value = `<span style="color:#c0392b;font-weight:700">✗</span>`;
            else if (value.indexOf("✓") > -1) value = `<span style="color:#157347;font-weight:700">✓</span>`;
        }

        // 3) "Diff on" -> amber when something differs
        if (column.fieldname === "diff_on" && data && data.diff_on) {
            value = `<span style="color:#b7791f;font-weight:600">${value}</span>`;
        }

        // 4) Presence -> amber when not "Both"
        if (column.fieldname === "presence" && data && data.presence && data.presence !== "Both") {
            value = `<span style="color:#b7791f;font-weight:600">${value}</span>`;
        }

        // 5) Sync note (skip reason) -> red
        if (column.fieldname === "sync_note" && data && data.sync_note) {
            value = `<span style="color:#c0392b;font-weight:600">${value}</span>`;
        }
        return value;
    },
};