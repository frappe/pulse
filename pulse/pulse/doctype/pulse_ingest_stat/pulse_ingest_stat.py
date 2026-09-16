# Copyright (c) 2025, hello@frappe.io and contributors
# For license information, please see license.txt

"""One hour of ingest counters, copied from Redis by `pulse.metrics.flush_ingest_stats`."""

from frappe.model.document import Document


class PulseIngestStat(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		accepted: DF.Int
		dropped: DF.Int
		failed_requests: DF.Int
		hour: DF.Datetime | None
		insert_max_ms: DF.Float
		insert_p50_ms: DF.Float
		insert_p95_ms: DF.Float
		rejected: DF.Int
		request_max_ms: DF.Float
		request_p50_ms: DF.Float
		request_p95_ms: DF.Float
		requests: DF.Int
		retries: DF.Int
	# end: auto-generated types

	pass
