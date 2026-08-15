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

    onload: function (report) {
        const M = "woocommerce_fusion.woocommerce.report.woo_translation_reconcile.woo_translation_reconcile";

        const $btn = report.page.add_inner_button(__("Translate untranslated"), function () {
            const sku = (frappe.query_report.get_filter_value
                ? frappe.query_report.get_filter_value("sku") : "") || "";

            frappe.prompt(
                [{
                    fieldname: "limit", fieldtype: "Int", reqd: 1, default: 50,
                    label: __("How many to translate now? (batch — AI cost per product)"),
                }],
                function (v) {
                    frappe.call({
                        method: M + ".translate_untranslated",
                        args: { sku: sku, limit: v.limit },
                        freeze: true,
                        freeze_message: __("Queuing translation…"),
                        callback: function (r) {
                            const d = (r && r.message) || {};
                            if (!d.queued && !d.running) {
                                frappe.msgprint(d.message || __("Nothing to translate for this scope."));
                                return;
                            }
                            const TITLE = __("Translating products");
                            frappe.show_progress(TITLE, 1, 100, d.message || __("Starting…"));
                            let done = false;
                            const t = setInterval(function () {
                                frappe.call({
                                    method: M + ".is_translation_running",
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
                                        frappe.msgprint({
                                            title: __("Translation finished"),
                                            indicator: (st.result && st.result.success === false) ? "orange" : "green",
                                            message: res,
                                        });
                                        frappe.query_report.refresh();
                                    },
                                });
                            }, 4000);
                        },
                    });
                },
                __("Translate untranslated"), __("Start")
            );
        });
        $btn.addClass("btn-primary");
    },

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