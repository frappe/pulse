# Copyright (c) 2026, hello@frappe.io and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


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
