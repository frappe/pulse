# Copyright (c) 2026, hello@frappe.io and Contributors
# See license.txt

"""The `Pulse Event` view, read by a real MariaDB user granted SELECT on the view alone."""

import uuid

import frappe
import pymysql
from frappe.tests import IntegrationTestCase

APPS = ["tenancy_test_a1", "tenancy_test_a2", "tenancy_test_b1", "tenancy_test_orphan"]


class IntegrationTestEventView(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not (frappe.conf.get("mariadb_root_password") or frappe.conf.get("root_password")):
			raise cls.skipTest(cls, "needs root_password in the site config to create a MariaDB user")

		from frappe.database.mariadb.setup_db import get_root_connection

		cls.root = get_root_connection()
		cls.username = f"pt_{uuid.uuid4().hex[:12]}"
		cls.password = uuid.uuid4().hex
		cls.root.sql("CREATE USER %s@'%%' IDENTIFIED BY %s", (cls.username, cls.password))
		cls.root.sql(f"GRANT SELECT ON `{frappe.conf.db_name}`.`Pulse Event` TO %s@'%%'", (cls.username,))

	@classmethod
	def tearDownClass(cls):
		cls.root.sql("DROP USER IF EXISTS %s@'%%'", (cls.username,))
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		self._clear()
		self.tenant = self._tenant("Tenancy Test A", ["tenancy_test_a1", "tenancy_test_a2"])
		self._tenant("Tenancy Test B", ["tenancy_test_b1"])
		self.database_user = frappe.get_doc(
			doctype="Pulse Database User", tenant=self.tenant.name, username=self.username, status="Active"
		).insert()
		frappe.db.bulk_insert(
			"Pulse Event",
			["name", "event_name", "app", "captured_at"],
			[(f"tenancy-test-{app}", "tenancy_test", app, frappe.utils.now_datetime()) for app in APPS],
		)
		frappe.db.commit()

	def tearDown(self):
		self._clear()
		super().tearDown()

	def _clear(self):
		frappe.db.delete("Pulse Event", {"app": ("in", APPS)})
		frappe.db.delete("Pulse Database User", {"tenant": ("like", "Tenancy Test%")})
		for name in frappe.get_all("Pulse Tenant", {"name": ("like", "Tenancy Test%")}, pluck="name"):
			frappe.delete_doc("Pulse Tenant", name, force=True)
		frappe.db.commit()

	def _tenant(self, name, apps):
		return frappe.get_doc(
			doctype="Pulse Tenant", tenant_name=name, apps=[{"app": app} for app in apps]
		).insert()

	def _query(self, sql):
		conn = pymysql.connect(
			user=self.username,
			password=self.password,
			database=frappe.conf.db_name,
			unix_socket=frappe.conf.db_socket,
			host=frappe.conf.db_host or "127.0.0.1",
			port=int(frappe.conf.db_port or 3306),
		)
		try:
			with conn.cursor() as cursor:
				cursor.execute(sql)
				return [row[0] for row in cursor.fetchall()]
		finally:
			conn.close()

	def _visible_apps(self):
		return self._query("select app from `Pulse Event` order by app")

	def test_sees_only_its_tenants_apps(self):
		self.assertEqual(self._visible_apps(), ["tenancy_test_a1", "tenancy_test_a2"])

	def test_cannot_read_the_table(self):
		with self.assertRaises(pymysql.err.OperationalError):
			self._query("select app from `tabPulse Event`")

	def test_archived_database_user_sees_nothing(self):
		self.database_user.db_set("status", "Archived", commit=True)
		self.assertEqual(self._visible_apps(), [])

	def test_disabled_tenant_sees_nothing(self):
		self.tenant.db_set("status", "Disabled", commit=True)
		self.assertEqual(self._visible_apps(), [])
