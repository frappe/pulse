# Copyright (c) 2026, hello@frappe.io and Contributors
# See license.txt

from unittest.mock import patch

import frappe
import frappe.share
from frappe.tests import IntegrationTestCase

from pulse.database_access import sync_pending

MEMBER = "tenancy-member@example.com"
OUTSIDER = "tenancy-outsider@example.com"


class FakePress:
	"""Stands in for Press's `press.api.client` over HTTP."""

	def __init__(self):
		self.calls = []
		self.remote = {}

	def __call__(self, url, headers, json, timeout):
		self.calls.append((url.rsplit(".", 1)[1], headers, json))
		method = url.rsplit(".", 1)[1]
		if method == "insert":
			name = f"sdu-{len(self.remote) + 1}"
			self.remote[name] = {"name": name, "status": "Pending"}
			return {"message": self.remote[name]}
		if method == "get":
			return {"message": self.remote[json["name"]]}
		if json["method"] == "get_credential":
			return {
				"message": {
					"host": "proxy.press.test",
					"port": 3306,
					"database": "_pulse",
					"username": f"u_{json['dn']}",
					"password": f"p_{json['dn']}",
					"mode": "granular",
					"max_connections": 2,
				}
			}
		self.remote[json["dn"]]["status"] = "Archived"
		return {"message": None}

	def methods(self):
		return [(method, json.get("method")) for method, _, json in self.calls]


class IntegrationTestDatabaseAccess(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.db.rollback)
		settings = frappe.get_single("Pulse Settings")
		settings.update(
			{
				"press_url": "https://press.test/",
				"press_site": "pulse.press.test",
				"press_api_key": "key",
				"press_api_secret": "secret",
			}
		)
		settings.save()
		for email in (MEMBER, OUTSIDER):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					doctype="User", email=email, first_name="Tenancy", send_welcome_email=0
				).insert()
		self.tenant = frappe.get_doc(
			doctype="Pulse Tenant", tenant_name="Tenancy Access Test", members=[{"user": MEMBER}]
		).insert()
		self.press = FakePress()
		patcher = patch("pulse.database_access.make_post_request", side_effect=self.press)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.addCleanup(frappe.set_user, "Administrator")

	def _create(self):
		return frappe.get_doc("Pulse Database User", self.tenant.create_database_user())

	def _activate(self, db_user):
		self.press.remote[db_user.site_database_user]["status"] = "Active"
		sync_pending()
		return frappe.get_doc("Pulse Database User", db_user.name)

	def test_create_inserts_a_pending_user(self):
		db_user = self._create()

		self.assertEqual((db_user.status, db_user.site_database_user), ("Pending", "sdu-1"))
		url, headers, payload = self.press.calls[0]
		self.assertEqual(url, "insert")
		self.assertEqual(headers, {"Authorization": "token key:secret"})
		self.assertEqual(
			payload["doc"],
			{
				"doctype": "Site Database User",
				"site": "pulse.press.test",
				"mode": "granular",
				"max_connections": 2,
				"label": "Tenancy Access Test",
				"permissions": [{"table": "Pulse Event", "mode": "read_only", "allow_all_columns": 1}],
			},
		)

	def test_active_user_stores_its_credential(self):
		db_user = self._activate(self._create())

		self.assertEqual(db_user.status, "Active")
		self.assertEqual(
			self.tenant.get_credential(),
			{
				"host": "proxy.press.test",
				"port": 3306,
				"database": "_pulse",
				"username": "u_sdu-1",
				"password": "p_sdu-1",
			},
		)

	def test_failed_user_keeps_the_reason(self):
		db_user = self._create()
		self.press.remote["sdu-1"].update(status="Failed", failure_reason="No quota")
		sync_pending()

		db_user.reload()
		self.assertEqual((db_user.status, db_user.failure_reason), ("Failed", "No quota"))

	def test_user_archived_on_press_is_archived(self):
		db_user = self._create()
		self.press.remote["sdu-1"]["status"] = "Archived"
		sync_pending()

		db_user.reload()
		self.assertEqual((db_user.status, bool(db_user.archived_on)), ("Archived", True))

	def test_pending_user_stays_pending(self):
		db_user = self._create()
		sync_pending()

		db_user.reload()
		self.assertEqual(db_user.status, "Pending")

	def test_rotation_archives_the_old_user_once_the_new_is_active(self):
		old = self._activate(self._create())
		new = self._create()
		old.reload()
		self.assertEqual(old.status, "Active")

		self._activate(new)

		old.reload()
		self.assertEqual((old.status, bool(old.archived_on)), ("Archived", True))
		self.assertEqual(self.press.remote["sdu-1"]["status"], "Archived")
		self.assertEqual(self.tenant.get_credential()["username"], "u_sdu-2")

	def test_second_pending_user_is_rejected(self):
		self._create()
		with self.assertRaises(frappe.ValidationError):
			self._create()

	def test_revoke_archives(self):
		db_user = self._activate(self._create())
		self.tenant.revoke_database_user()

		db_user.reload()
		self.assertEqual(db_user.status, "Archived")
		self.assertIn(("run_doc_method", "archive"), self.press.methods())

	def test_disabling_the_tenant_archives(self):
		db_user = self._activate(self._create())
		self.tenant.status = "Disabled"
		self.tenant.save()

		db_user.reload()
		self.assertEqual(db_user.status, "Archived")
		self.assertIn(("run_doc_method", "archive"), self.press.methods())

	def test_members_read_the_tenant_and_its_credential(self):
		self._activate(self._create())

		frappe.set_user(MEMBER)
		tenant = frappe.get_doc("Pulse Tenant", self.tenant.name, check_permission=True)
		self.assertEqual(tenant.get_credential()["username"], "u_sdu-1")

	def test_outsiders_cannot_read_the_credential(self):
		self._activate(self._create())

		frappe.set_user(OUTSIDER)
		self.assertFalse(frappe.has_permission("Pulse Tenant", doc=self.tenant.name))
		with self.assertRaises(frappe.PermissionError):
			self.tenant.get_credential()

	def test_removed_member_loses_access(self):
		self.tenant.members = []
		self.tenant.save()

		self.assertFalse(frappe.has_permission("Pulse Tenant", doc=self.tenant.name, user=MEMBER))

	def test_saving_keeps_shares_given_by_hand(self):
		frappe.share.add_docshare("Pulse Tenant", self.tenant.name, OUTSIDER)
		self.tenant.members = []
		self.tenant.save()

		self.assertTrue(frappe.has_permission("Pulse Tenant", doc=self.tenant.name, user=OUTSIDER))
