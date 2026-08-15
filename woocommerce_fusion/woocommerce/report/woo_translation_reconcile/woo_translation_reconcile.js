// Filters + colouring for the "Woo Translation Reconcile" script report.
frappe.query_reports["Woo Translation Reconcile"] = {
    filters: [
        {
            fieldname: "sku",
            label: __("SKU contains"),
            fieldtype: "Data",
        },
        {
            fieldname: "issue",
            label: __("Issue"),
            fieldtype: "Select",
            options: ["", "Missing English", "Name", "Description", "Compatibility"].join("\n"),
        },
        {
            fieldname: "only_issues",
            label: __("Only issues"),
            fieldtype: "Check",
            default: 1,
        },
        {
            fieldname: "max_products",
            label: __("Max rows (blank = all)"),
            fieldtype: "Int",
        },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        // English columns -> blue
        if (["en_name", "en_desc", "en_id"].includes(column.fieldname)) {
            value = `<span style="color:#0b5cff">${value}</span>`;
        }

        // Issue -> red (Missing English in bolder red)
        if (column.fieldname === "issue" && data && data.issue) {
            const strong = data.issue.indexOf("Missing English") > -1;
            value = `<span style="color:#c0392b;font-weight:${strong ? 700 : 600}">${value}</span>`;
        }

        // Whole row tint when it's an issue (subtle) via SKU cell marker
        if (column.fieldname === "sku" && data && data.has_issue) {
            value = `<span style="font-weight:600">${value}</span>`;
        }
        return value;
    },
};