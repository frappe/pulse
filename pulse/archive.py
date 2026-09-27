"""Moves months of Pulse Event past the retention window to Parquet files in the site's private files.

A month is grouped by `creation`, the receive time, so once it is past no event can
join it and its file is written once. Rows are deleted only after the file is read
back and holds every row of the month.
"""

import os
from itertools import batched

import frappe
import pyarrow as pa
import pyarrow.parquet as pq
from frappe.model import no_value_fields
from frappe.query_builder.functions import Min
from frappe.utils import add_days, add_months, get_datetime, getdate
from frappe.utils.synchronization import filelock

RETENTION_DAYS = 180
PAGE_SIZE = 50_000
# One batch per transaction, so a delete never holds locks that stall ingest for long.
DELETE_BATCH_SIZE = 10_000

# An event field of any other type raises, so a new field is never deleted without being archived.
ARROW_TYPES = {
	"Data": pa.string(),
	"JSON": pa.string(),
	"Datetime": pa.timestamp("us"),
	"Check": pa.int8(),
}


class ArchiveMismatch(Exception):
	pass


def archive_events():
	"""Archive the oldest due month, one per run so a run fits the long queue's timeout."""
	if month := due_month():
		archive_month(month)


def due_month(today=None):
	"""The oldest month whose every event is older than the retention window, as YYYY-MM."""
	event = frappe.qb.DocType("Pulse Event")
	oldest = frappe.qb.from_(event).select(Min(event.creation)).run()[0][0]
	if not oldest:
		return None
	start, end = _bounds(oldest.strftime("%Y-%m"))
	if end > get_datetime(add_days(getdate(today), -RETENTION_DAYS)):
		return None
	return start.strftime("%Y-%m")


def archive_month(month):
	# Two runs on one month would write the same file while one of them deletes its rows.
	with filelock("pulse_archive", timeout=1):
		status = frappe.db.get_value("Pulse Archive", month, "status")
		if status == "Purged":
			frappe.throw(f"Pulse Event has rows in {month}, which is already archived")
		if not status:
			_export(month)
		_delete(month)
		frappe.db.set_value("Pulse Archive", month, "status", "Purged")
		frappe.db.commit()


def file_path(month):
	return os.path.join("private", "files", "pulse_archive", "pulse_event", f"{month}.parquet")


def _export(month):
	start, end = _bounds(month)
	schema = _schema()
	path = frappe.get_site_path(file_path(month))
	os.makedirs(os.path.dirname(path), exist_ok=True)

	# Written aside and moved into place, so a file at `path` is always complete.
	partial = f"{path}.partial"
	with pq.ParquetWriter(partial, schema, compression="zstd") as writer, frappe.db.unbuffered_cursor():
		event = frappe.qb.DocType("Pulse Event")
		rows = (
			frappe.qb.from_(event)
			.select(*(event[field.name] for field in schema))
			.where((event.creation >= start) & (event.creation < end))
			.orderby(event.creation)
			.run(as_list=True, as_iterator=True)
		)
		for page in batched(rows, PAGE_SIZE):
			writer.write_batch(pa.RecordBatch.from_arrays(list(zip(*page, strict=True)), schema=schema))

	expected = _count(start, end)
	written = pq.ParquetFile(partial).metadata.num_rows
	if written != expected:
		os.remove(partial)
		raise ArchiveMismatch(f"{month}: file has {written} rows, Pulse Event has {expected}")
	os.replace(partial, path)

	frappe.get_doc(
		doctype="Pulse Archive",
		month=month,
		status="Exported",
		row_count=written,
		file_path=file_path(month),
	).insert(ignore_permissions=True)
	frappe.db.commit()


def _delete(month):
	start, end = _bounds(month)
	while names := frappe.get_all(
		"Pulse Event",
		[["creation", ">=", start], ["creation", "<", end]],
		pluck="name",
		limit=DELETE_BATCH_SIZE,
		order_by="creation",
	):
		frappe.db.delete("Pulse Event", {"name": ("in", names)})
		frappe.db.commit()


def _schema():
	fields = [
		(field.fieldname, ARROW_TYPES[field.fieldtype])
		for field in frappe.get_meta("Pulse Event").fields
		if field.fieldtype not in no_value_fields
	]
	return pa.schema([("name", pa.string()), *fields, ("creation", pa.timestamp("us"))])


def _count(start, end):
	return frappe.db.count("Pulse Event", [["creation", ">=", start], ["creation", "<", end]])


def _bounds(month):
	start = get_datetime(f"{month}-01")
	return start, get_datetime(add_months(start, 1))
