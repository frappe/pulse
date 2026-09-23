"""Provisions each tenant's MariaDB user, which reads events through the `Pulse Event` view."""

import frappe
from frappe import _
from frappe.integrations.utils import make_post_request
from frappe.utils import now_datetime

CREDENTIAL_FIELDS = ("host", "port", "database", "username", "password")


class PressDatabaseAccess:
	"""A tenant's database user is a Press `Site Database User` on the Pulse site."""

	def create(self, tenant):
		remote = self._call(
			"insert",
			doc={
				"doctype": "Site Database User",
				"site": self._settings().press_site,
				"mode": "granular",
				"max_connections": 2,
				"label": tenant.tenant_name,
				"permissions": [{"table": "Pulse Event", "mode": "read_only", "allow_all_columns": 1}],
			},
		)
		return frappe.get_doc(
			doctype="Pulse Database User",
			tenant=tenant.name,
			status="Pending",
			site_database_user=remote["name"],
		).insert()

	def revoke(self, db_user):
		self._run(db_user, "archive")
		db_user.status = "Archived"
		db_user.archived_on = now_datetime()
		db_user.save()

	def credential(self, db_user):
		return {
			field: db_user.get_password(field) if field == "password" else db_user.get(field)
			for field in CREDENTIAL_FIELDS
		}

	def sync(self, db_user):
		remote = self._call("get", doctype="Site Database User", name=db_user.site_database_user)
		status = remote["status"]
		if status == "Active":
			credential = self._run(db_user, "get_credential")
			for older in frappe.get_all(
				"Pulse Database User",
				{"tenant": db_user.tenant, "status": "Active", "name": ("!=", db_user.name)},
				pluck="name",
			):
				self.revoke(frappe.get_doc("Pulse Database User", older))
			db_user.update({field: credential[field] for field in CREDENTIAL_FIELDS})
		elif status == "Failed":
			db_user.failure_reason = remote.get("failure_reason")
		elif status == "Archived":
			db_user.archived_on = now_datetime()
		else:
			return
		db_user.status = status
		db_user.save()

	def _run(self, db_user, method):
		return self._call(
			"run_doc_method", dt="Site Database User", dn=db_user.site_database_user, method=method
		)

	def _call(self, method, /, **data):
		settings = self._settings()
		return make_post_request(
			f"{settings.press_url.rstrip('/')}/api/method/press.api.client.{method}",
			headers={
				"Authorization": f"token {settings.press_api_key}:{settings.get_password('press_api_secret')}"
			},
			json=data,
			timeout=30,
		).get("message")

	def _settings(self):
		settings = frappe.get_cached_doc("Pulse Settings")
		if not (settings.press_url and settings.press_site and settings.press_api_key):
			frappe.throw(_("Set Press URL, Press Site, API Key and API Secret in Pulse Settings"))
		return settings


def sync_pending():
	access = PressDatabaseAccess()
	for name in frappe.get_all("Pulse Database User", {"status": "Pending"}, pluck="name"):
		try:
			access.sync(frappe.get_doc("Pulse Database User", name))
		except Exception:
			frappe.log_error(f"Pulse Database User {name} sync failed")
