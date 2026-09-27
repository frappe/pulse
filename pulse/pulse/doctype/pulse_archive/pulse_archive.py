# Copyright (c) 2026, hello@frappe.io and contributors
# For license information, please see license.txt

"""One month of Pulse Event moved to a Parquet file by `pulse.archive.archive_events`."""

from frappe.model.document import Document


class PulseArchive(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		file_path: DF.Data | None
		month: DF.Data | None
		row_count: DF.Int
		status: DF.Literal["Exported", "Purged"]
	# end: auto-generated types

	pass
