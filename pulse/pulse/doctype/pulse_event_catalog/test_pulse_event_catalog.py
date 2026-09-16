# Copyright (c) 2025, hello@frappe.io and Contributors
# See license.txt

"""The catalog builds itself from consumed events; these cover what it records."""

import uuid

import frappe
from frappe.tests import IntegrationTestCase

from pulse.pulse.doctype.pulse_event.pulse_event import (
	consume_pulse_events,
	prepare_event,
	store_events,
)
from pulse.pulse.doctype.redis_stream.redis_stream import RedisStream


class IntegrationTestPulseEventCatalog(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		token = uuid.uuid4().hex
		self.prefix = f"test_{token}_"
		frappe.flags.test_stream_name = f"test_catalog_{token}"
		self.stream = RedisStream.init()
		# The stream path is what these tests drain; Direct mode would store on ingest.
		self.ingest_mode = frappe.db.get_single_value("Pulse Settings", "ingest_mode")
		frappe.db.set_single_value("Pulse Settings", "ingest_mode", "Redis Stream")

	def tearDown(self):
		frappe.db.set_single_value("Pulse Settings", "ingest_mode", self.ingest_mode)
		self.stream.delete()
		frappe.flags.test_stream_name = None
		frappe.db.delete("Pulse Event", {"event_name": ("like", f"{self.prefix}%")})
		frappe.db.delete("Pulse Event Catalog", {"event_name": ("like", f"{self.prefix}%")})
		frappe.db.commit()
		super().tearDown()

	def _enqueue(self, name, **kwargs):
		kwargs.setdefault("captured_at", frappe.utils.now_datetime())
		store_events([prepare_event(event_name=f"{self.prefix}{name}", **kwargs)])

	def _entry(self, name):
		return frappe.get_doc("Pulse Event Catalog", f"{self.prefix}{name}")

	def test_a_new_name_records_itself(self):
		self._enqueue("signup_viewed", app="press")

		consume_pulse_events()

		entry = self._entry("signup_viewed")
		self.assertEqual(entry.status, "Discovered")
		self.assertEqual(entry.event_count, 1)
		self.assertEqual(entry.apps, "press")
		self.assertIsNotNone(entry.first_seen)

	def test_counts_accumulate_across_drains(self):
		for _ in range(3):
			self._enqueue("pageview", app="website")
		consume_pulse_events()

		self._enqueue("pageview", app="website")
		consume_pulse_events()

		self.assertEqual(self._entry("pageview").event_count, 4)

	def test_records_every_app_a_name_is_sent_from(self):
		# Two apps reporting one name is the signal that two call sites disagree.
		self._enqueue("ticket_created", app="helpdesk")
		self._enqueue("ticket_created", app="crm")

		consume_pulse_events()

		self.assertEqual(self._entry("ticket_created").apps, "crm, helpdesk")

	def test_review_status_survives_later_events(self):
		self._enqueue("course_progress", app="lms")
		consume_pulse_events()
		frappe.db.set_value("Pulse Event Catalog", f"{self.prefix}course_progress", "status", "Approved")

		self._enqueue("course_progress", app="lms")
		consume_pulse_events()

		entry = self._entry("course_progress")
		self.assertEqual(entry.status, "Approved")
		self.assertEqual(entry.event_count, 2)

	def test_a_failing_catalog_update_does_not_lose_events(self):
		from unittest.mock import patch

		self._enqueue("resilient", app="frappe")

		with patch(
			"pulse.pulse.doctype.pulse_event_catalog.pulse_event_catalog._record_events",
			side_effect=RuntimeError("catalog is broken"),
		):
			consume_pulse_events()

		self.assertEqual(frappe.db.count("Pulse Event", {"event_name": f"{self.prefix}resilient"}), 1)
