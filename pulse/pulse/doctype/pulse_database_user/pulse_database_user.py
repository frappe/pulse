# Copyright (c) 2026, hello@frappe.io and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PulseDatabaseUser(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		archived_on: DF.Datetime | None
		database: DF.Data | None
		failure_reason: DF.SmallText | None
		host: DF.Data | None
		password: DF.Password | None
		port: DF.Int
		site_database_user: DF.Data | None
		status: DF.Literal["Pending", "Active", "Failed", "Archived"]
		tenant: DF.Link
		username: DF.Data | None
	# end: auto-generated types

	pass
