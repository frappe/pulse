# Copyright (c) 2026, hello@frappe.io and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestPulseTenant(IntegrationTestCase):
	def _tenant(self, name, apps):
		return frappe.get_doc(doctype="Pulse Tenant", tenant_name=name, apps=[{"app": app} for app in apps])

	def test_app_is_unique_within_a_tenant(self):
		with self.assertRaises(frappe.ValidationError):
			self._tenant("Tenancy Test Dup", ["tenancy_test_x", "tenancy_test_x"]).insert()

	def test_app_is_unique_across_tenants(self):
		self._tenant("Tenancy Test One", ["tenancy_test_x"]).insert()
		with self.assertRaises(frappe.ValidationError):
			self._tenant("Tenancy Test Two", ["tenancy_test_x"]).insert()

	def test_tenant_without_apps_saves(self):
		self._tenant("Tenancy Test Empty", []).insert()
