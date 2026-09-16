# Copyright (c) 2025, hello@frappe.io and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class PulseSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		anon_salt: DF.Data | None
		anon_salt_day: DF.Data | None
		api_key: DF.Password | None
		ingest_mode: DF.Literal["Direct", "Redis Stream"]
		max_stream_length: DF.Int
		rate_limit: DF.Int
	# end: auto-generated types

	pass
