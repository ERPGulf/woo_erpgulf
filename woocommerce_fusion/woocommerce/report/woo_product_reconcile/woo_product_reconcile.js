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
            default: 0,
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
        },
        {
            fieldname: "f_universal",
            label: __("Universal"),
            fieldtype: "Select",
            options: "\nYes\nNo",
        },
    ],

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
        return value;
    },
};