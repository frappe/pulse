# Copyright (c) 2026, hello@frappe.io and Contributors
# See license.txt

"""Archive: which month is due, what its file holds, and that rows leave only once the file is complete."""

import os
from datetime import date, datetime
from unittest.mock import patch

import frappe
import pyarrow.parquet as pq
from frappe.tests import IntegrationTestCase
from frappe.utils.synchronization import LockTimeoutError, filelock

from pulse import archive
from pulse.pulse.doctype.pulse_event.pulse_event import _INSERT_FIELDS, _row

# Far from real traffic, so neither the rows nor the files collide with it.
MONTH = "2001-01"
NEXT_MONTH = "2001-02"


class IntegrationTestArchive(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._clear()

	def tearDown(self):
		self._clear()
		super().tearDown()

	def _clear(self):
		frappe.db.delete("Pulse Event", {"event_name": "archive_test"})
		frappe.db.delete("Pulse Archive", {"month": ("in", [MONTH, NEXT_MONTH])})
		for month in (MONTH, NEXT_MONTH):
			path = frappe.get_site_path(archive.file_path(month))
			if os.path.exists(path):
				os.remove(path)
		frappe.db.commit()

	def _insert(self, name, received_at, **data):
		event = {"event_name": "archive_test", "captured_at": received_at, "app": "pulse", **data}
		frappe.db.bulk_insert("Pulse Event", _INSERT_FIELDS, [_row(name, event, received_at)])
		frappe.db.commit()

	def _names(self, start, end):
		return frappe.get_all(
			"Pulse Event", {"creation": ("between", [start, end])}, pluck="name", order_by="name"
		)

	def _file_names(self, month):
		return sorted(
			pq.read_table(frappe.get_site_path(archive.file_path(month))).column("name").to_pylist()
		)

	def test_due_month_is_the_oldest_month_past_retention(self):
		self._insert("a1", datetime(2001, 1, 31, 23, 59))
		self._insert("b1", datetime(2001, 2, 10))

		self.assertIsNone(archive.due_month(today=date(2001, 7, 30)))
		self.assertEqual(archive.due_month(today=date(2001, 7, 31)), MONTH)

	def test_moves_every_event_of_the_month_to_its_file(self):
		self._insert("a1", datetime(2001, 1, 1), properties='{"k": 1}')
		self._insert("a2", datetime(2001, 1, 31, 23, 59, 59), is_internal=1)
		self._insert("b1", datetime(2001, 2, 1))

		with patch("pulse.archive.PAGE_SIZE", 1), patch("pulse.archive.DELETE_BATCH_SIZE", 1):
			archive.archive_month(MONTH)

		table = pq.read_table(frappe.get_site_path(archive.file_path(MONTH)))
		rows = {row["name"]: row for row in table.to_pylist()}
		self.assertEqual(set(rows), {"a1", "a2"})
		self.assertEqual(rows["a1"]["properties"], '{"k": 1}')
		self.assertEqual(rows["a2"]["is_internal"], 1)
		self.assertEqual(rows["a2"]["creation"], datetime(2001, 1, 31, 23, 59, 59))

		self.assertEqual(self._names("2001-01-01", "2001-12-31"), ["b1"])
		record = frappe.get_doc("Pulse Archive", MONTH)
		self.assertEqual(record.status, "Purged")
		self.assertEqual(record.row_count, 2)
		self.assertEqual(record.file_path, archive.file_path(MONTH))

	def test_keeps_the_rows_when_the_file_is_short(self):
		self._insert("a1", datetime(2001, 1, 5))
		self._insert("a2", datetime(2001, 1, 6))

		with patch("pulse.archive._count", return_value=3), self.assertRaises(archive.ArchiveMismatch):
			archive.archive_month(MONTH)

		self.assertEqual(self._names("2001-01-01", "2001-01-31"), ["a1", "a2"])
		self.assertFalse(frappe.db.exists("Pulse Archive", MONTH))

	def test_resumes_deleting_without_rewriting_the_file(self):
		self._insert("a1", datetime(2001, 1, 5))
		self._insert("a2", datetime(2001, 1, 6))

		with patch("pulse.archive._delete", side_effect=RuntimeError):
			self.assertRaises(RuntimeError, archive.archive_month, MONTH)
		self.assertEqual(frappe.db.get_value("Pulse Archive", MONTH, "status"), "Exported")

		# A partial delete: the file must still hold both rows after the retry.
		frappe.db.delete("Pulse Event", {"name": "a1"})
		frappe.db.commit()
		archive.archive_month(MONTH)

		self.assertEqual(self._file_names(MONTH), ["a1", "a2"])
		self.assertEqual(self._names("2001-01-01", "2001-01-31"), [])
		self.assertEqual(frappe.db.get_value("Pulse Archive", MONTH, "status"), "Purged")

	def test_refuses_rows_that_arrive_in_a_purged_month(self):
		self._insert("a1", datetime(2001, 1, 5))
		archive.archive_month(MONTH)
		self._insert("a2", datetime(2001, 1, 6))

		self.assertRaises(frappe.ValidationError, archive.archive_month, MONTH)
		self.assertEqual(self._names("2001-01-01", "2001-01-31"), ["a2"])
		self.assertEqual(self._file_names(MONTH), ["a1"])

	def test_runs_one_archive_at_a_time(self):
		self._insert("a1", datetime(2001, 1, 5))

		with filelock("pulse_archive"):
			self.assertRaises(LockTimeoutError, archive.archive_month, MONTH)

		self.assertEqual(self._names("2001-01-01", "2001-01-31"), ["a1"])
		self.assertFalse(frappe.db.exists("Pulse Archive", MONTH))
