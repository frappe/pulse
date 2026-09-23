// Copyright (c) 2026, hello@frappe.io and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pulse Tenant", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Active" || !frm.perm[0]?.write) return;

		const statuses = frm.doc.__onload?.database_user_statuses || [];
		const call = (method) => frm.call(method).then(() => frm.reload_doc());
		const group = __("Database User");

		if (statuses.includes("Active") && !statuses.includes("Pending")) {
			frm.add_custom_button(__("Rotate"), () => call("create_database_user"), group);
		}
		if (!statuses.length) {
			frm.add_custom_button(__("Provision"), () => call("create_database_user"), group);
		}
		if (statuses.length) {
			frm.add_custom_button(
				__("Revoke"),
				() =>
					frappe.confirm(__("Archive this tenant's database user?"), () =>
						call("revoke_database_user")
					),
				group
			);
		}
	},
});
