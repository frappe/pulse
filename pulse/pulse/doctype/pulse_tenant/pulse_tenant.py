# Copyright (c) 2026, hello@frappe.io and contributors
# For license information, please see license.txt

import frappe
import frappe.share
from frappe import _
from frappe.model.document import Document

from pulse.database_access import PressDatabaseAccess


class PulseTenant(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from pulse.pulse.doctype.pulse_tenant_app.pulse_tenant_app import PulseTenantApp
		from pulse.pulse.doctype.pulse_tenant_member.pulse_tenant_member import PulseTenantMember

		apps: DF.Table[PulseTenantApp]
		members: DF.TableMultiSelect[PulseTenantMember]
		status: DF.Literal["Active", "Disabled"]
		tenant_name: DF.Data
	# end: auto-generated types

	def validate(self):
		apps = [row.app for row in self.apps]
		if duplicate := next((app for app in apps if apps.count(app) > 1), None):
			frappe.throw(_("App {0} is listed more than once").format(frappe.bold(duplicate)))

		owned = frappe.get_all(
			"Pulse Tenant App",
			filters={"parenttype": "Pulse Tenant", "app": ("in", apps), "parent": ("!=", self.name)},
			fields=["app", "parent"],
			limit=1,
		)
		if owned:
			frappe.throw(
				_("App {0} already belongs to tenant {1}").format(
					frappe.bold(owned[0].app), frappe.bold(owned[0].parent)
				)
			)

	def onload(self):
		self.set_onload("database_user_statuses", self._database_users("status", ("Pending", "Active")))

	def on_update(self):
		self._share_with_members()
		if self.has_value_changed("status") and self.status == "Disabled":
			self._revoke_database_users()

	def _share_with_members(self):
		"""A member reads the tenant through a share: role permissions can't be narrowed to membership."""
		members = {row.user for row in self.members}
		shared = set(
			frappe.get_all("DocShare", {"share_doctype": self.doctype, "share_name": self.name}, pluck="user")
		)
		flags = {"ignore_share_permission": True}
		for user in members - shared:
			frappe.share.add_docshare(self.doctype, self.name, user, flags=flags)
		for user in shared - members:
			frappe.share.remove(self.doctype, self.name, user, flags=flags)

	def _database_users(self, field, statuses):
		return frappe.get_all(
			"Pulse Database User", {"tenant": self.name, "status": ("in", statuses)}, pluck=field
		)

	@frappe.whitelist()
	def create_database_user(self):
		"""Provisions the first user, or rotates: the Active one is archived once the new one is Active."""
		self.check_permission("write")
		if self.status == "Disabled":
			frappe.throw(_("Tenant {0} is disabled").format(frappe.bold(self.name)))
		if self._database_users("name", ("Pending",)):
			frappe.throw(_("A database user is already being created for this tenant"))
		return PressDatabaseAccess().create(self).name

	@frappe.whitelist()
	def revoke_database_user(self):
		self.check_permission("write")
		self._revoke_database_users()

	def _revoke_database_users(self):
		access = PressDatabaseAccess()
		for name in self._database_users("name", ("Pending", "Active")):
			access.revoke(frappe.get_doc("Pulse Database User", name))

	@frappe.whitelist()
	def get_credential(self):
		if (
			frappe.session.user not in {row.user for row in self.members}
			and "System Manager" not in frappe.get_roles()
		):
			raise frappe.PermissionError
		active = self._database_users("name", ("Active",))
		if not active:
			frappe.throw(_("Tenant {0} has no active database user").format(frappe.bold(self.name)))
		return PressDatabaseAccess().credential(frappe.get_doc("Pulse Database User", active[0]))
