# Copyright (c) 2025, hello@frappe.io and Contributors
# See license.txt

import uuid
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase  # type: ignore
from frappe.utils.background_jobs import get_redis_conn

from pulse.pulse.doctype.pulse_event.pulse_event import (
	consume_pulse_events,
	prepare_event,
	store_events,
)
from pulse.pulse.doctype.redis_stream.redis_stream import RedisStream


class IntegrationTestPulseEvent(IntegrationTestCase):
	"""
	Integration tests for the Pulse Event ingest -> stream -> database pipeline.
	These tests use a real Redis instance (do not mock Redis). Each test gets a
	unique stream (via ``frappe.flags.test_stream_name``) and tags its events with
	a unique ``event_name`` prefix, so assertions and cleanup are scoped to the
	test's own rows and never touch existing site data.
	"""

	def setUp(self):
		super().setUp()
		token = uuid.uuid4().hex
		self.test_stream_name = f"test_pulse_event_{token}"
		self.prefix = f"test_{token}_"
		frappe.flags.test_stream_name = self.test_stream_name
		self.stream = RedisStream.init(name=self.test_stream_name)
		self.conn = get_redis_conn()
		# The stream path is what these tests drain; Direct mode would store on ingest.
		self.ingest_mode = frappe.db.get_single_value("Pulse Settings", "ingest_mode")
		frappe.db.set_single_value("Pulse Settings", "ingest_mode", "Redis Stream")

	def tearDown(self):
		frappe.db.set_single_value("Pulse Settings", "ingest_mode", self.ingest_mode)
		self.stream.delete()
		frappe.flags.test_stream_name = None
		# Only remove rows this test created; leave existing site data untouched.
		frappe.db.delete("Pulse Event", {"event_name": ("like", f"{self.prefix}%")})
		frappe.db.commit()
		super().tearDown()

	def _enqueue(self, name, **kwargs):
		kwargs.setdefault("captured_at", frappe.utils.now_datetime())
		store_events([prepare_event(event_name=f"{self.prefix}{name}", **kwargs)])

	def _count(self):
		return frappe.db.count("Pulse Event", {"event_name": ("like", f"{self.prefix}%")})

	def test_prepare_validates_required_fields(self):
		with self.assertRaises(frappe.ValidationError):
			prepare_event(event_name=None, captured_at=frappe.utils.now_datetime())
		with self.assertRaises(frappe.ValidationError):
			prepare_event(event_name="x", captured_at=None)

	def test_store_adds_entry_to_stream(self):
		self._enqueue(
			"signup",
			site="test-site",
			app="test-app",
			user="anon_testuser",
			team="team_test",
			properties={"key": "value"},
		)
		self.assertGreaterEqual(self.stream.get_length(), 1)

	def test_consume_writes_events_to_database(self):
		for i in range(3):
			self._enqueue(f"evt_{i}", site=f"site_{i}", properties={"i": i})

		consume_pulse_events()

		rows = frappe.get_all(
			"Pulse Event",
			filters={"event_name": ("like", f"{self.prefix}%")},
			fields=["name", "event_name", "site", "properties"],
		)
		self.assertEqual(len(rows), 3)
		names = {r.event_name for r in rows}
		self.assertEqual(names, {f"{self.prefix}evt_{i}" for i in range(3)})
		# name is the stream entry id; properties round-trips as valid JSON
		row = next(r for r in rows if r.event_name == f"{self.prefix}evt_1")
		self.assertEqual(frappe.parse_json(row.properties), {"i": 1})

	def test_consume_is_idempotent_on_redelivery(self):
		self._enqueue("once", site="s")

		# Simulate a crash after the insert commits but before XACK: the row is
		# written, but the entry stays pending and is redelivered on the next drain.
		with patch.object(RedisStream, "ack_entries", lambda self, ids: None):
			consume_pulse_events()
		self.assertEqual(self._count(), 1)

		# Second drain re-reads the pending entry and re-inserts it. ignore_duplicates
		# on the entry-id primary key keeps the table at exactly one row.
		consume_pulse_events()
		self.assertEqual(self._count(), 1)

	def test_consume_no_events_is_noop(self):
		consume_pulse_events()
		self.assertEqual(self._count(), 0)

	def test_consume_keeps_one_row_for_a_resent_event(self):
		# A batch resent after a timeout, or instrumentation firing twice, arrives as
		# two identical events. Each is a separate stream entry, so the entry-id
		# primary key can't catch it; the derived dedup key is what does.
		captured_at = frappe.utils.now_datetime()
		for _ in range(2):
			self._enqueue("resent", captured_at=captured_at, site="s", properties={"route": "/app"})

		consume_pulse_events()

		self.assertEqual(self._count(), 1)

	def test_consume_keeps_events_that_only_look_alike(self):
		# Same name and source, but a different moment or different properties — these
		# are distinct events and all of them have to survive.
		self._enqueue("similar", site="s", properties={"route": "/app"})
		self._enqueue("similar", site="s", properties={"route": "/app/todo"})
		self._enqueue("similar", site="s", properties={"route": "/app"})

		consume_pulse_events()

		self.assertEqual(self._count(), 3)
