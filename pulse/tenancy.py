import frappe


def create_event_view():
	"""Expose each tenant's rows of `tabPulse Event` through the `Pulse Event` view.

	A tenant's MariaDB user is granted SELECT on the view alone. The view runs as its
	definer, so it can read the underlying tables, while `USER()` still names the
	connecting user and scopes the rows to that user's tenant.
	"""
	frappe.db.add_index("Pulse Event", ["app", "captured_at"])
	frappe.db.sql_ddl(
		"""
		create or replace sql security definer view `Pulse Event` as
		select e.name, e.event_name, e.captured_at, e.site, e.user, e.app, e.team, e.properties
		from `tabPulse Event` e
		join `tabPulse Tenant App` ta on ta.app = e.app
		join `tabPulse Tenant` t on t.name = ta.parent and t.status = 'Active'
		join `tabPulse Database User` du on du.tenant = t.name and du.status = 'Active'
		where du.username = substring_index(user(), '@', 1)
		"""
	)
