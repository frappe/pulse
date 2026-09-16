# Copyright (c) 2025, hello@frappe.io and Contributors
# See license.txt

"""Ingest: what a request stores, and how a failure reaches the client."""

import uuid
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from pymysql.constants import ER
from pymysql.err import OperationalError

from pulse.api import _bulk_ingest, ingest
from pulse.capture import DROP, clear_rule_cache
from pulse.pulse.doctype.pulse_event.pulse_event import StorageUnavailable
from pulse.pulse.doctype.redis_stream.redis_stream import RedisStream


class IntegrationTestBulkIngest(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		token = uuid.uuid4().hex
		self.prefix = f"test_{token}_"
		frappe.flags.test_stream_name = f"test_bulk_ingest_{token}"
		self.stream = RedisStream.init()
		self.ingest_mode = frappe.db.get_single_value("Pulse Settings", "ingest_mode")
		self._set_ingest_mode("Direct")
		self.rules = []
		# The endpoint's auth is exercised in its own right; these tests are about
		# what happens to a batch once it is past the door.
		self.auth = patch("pulse.api.check_auth", lambda: None)
		self.auth.start()
		self.record_request = patch("pulse.metrics.record_request", create=True).start()

	def tearDown(self):
		patch.stopall()
		for rule in self.rules:
			frappe.delete_doc("Pulse Capture Rule", rule, force=True, ignore_permissions=True)
		clear_rule_cache()
		self._set_ingest_mode(self.ingest_mode)
		self.stream.delete()
		frappe.flags.test_stream_name = None
		frappe.db.delete("Pulse Event", {"event_name": ("like", f"{self.prefix}%")})
		frappe.db.delete("Pulse Event Catalog", {"event_name": ("like", f"{self.prefix}%")})
		frappe.db.commit()
		super().tearDown()

	def _set_ingest_mode(self, mode):
		frappe.db.set_single_value("Pulse Settings", "ingest_mode", mode)
		# Committed so a rollback on the storage failure path doesn't undo it.
		frappe.db.commit()

	def _event(self, name, **kwargs):
		kwargs.setdefault("captured_at", str(frappe.utils.now_datetime()))
		return {"event_name": f"{self.prefix}{name}", "site": "test-site", **kwargs}

	def _count(self):
		return frappe.db.count("Pulse Event", {"event_name": ("like", f"{self.prefix}%")})

	def _reported(self):
		self.record_request.assert_called_once()
		return self.record_request.call_args.kwargs

	def test_stores_events_before_responding(self):
		result = _bulk_ingest([self._event("one"), self._event("two")], browser_direct=False)

		self.assertEqual(result, {"accepted": 2, "dropped": 0, "rejected": []})
		self.assertEqual(self._count(), 2)
		self.assertEqual(self.stream.get_length(), 0)
		reported = self._reported()
		self.assertEqual(reported["accepted"], 2)
		self.assertIsNotNone(reported["insert_ms"])
		self.assertFalse(reported["failed"])

	def test_resent_batch_is_stored_once(self):
		batch = [self._event("one"), self._event("two")]

		_bulk_ingest(batch, browser_direct=False)
		_bulk_ingest(batch, browser_direct=False)

		self.assertEqual(self._count(), 2)

	def test_rejects_bad_event_and_keeps_the_rest(self):
		batch = [
			self._event("good_one"),
			{"event_name": None, "captured_at": str(frappe.utils.now_datetime())},
			self._event("good_two"),
		]

		result = _bulk_ingest(batch, browser_direct=False)

		self.assertEqual(result["accepted"], 2)
		self.assertEqual(len(result["rejected"]), 1)
		# The index locates the offender in the batch the client sent.
		self.assertEqual(result["rejected"][0]["index"], 1)
		self.assertEqual(self._count(), 2)

	def test_event_dropped_by_a_capture_rule_is_not_stored(self):
		rule = frappe.get_doc(
			{
				"doctype": "Pulse Capture Rule",
				"match_field": "Site",
				"match_operator": "Equals",
				"match_value": f"{self.prefix}noisy",
				"action": DROP,
			}
		).insert(ignore_permissions=True)
		self.rules.append(rule.name)

		result = _bulk_ingest(
			[self._event("kept"), self._event("noise", site=f"{self.prefix}noisy")], browser_direct=False
		)

		self.assertEqual((result["accepted"], result["dropped"]), (1, 1))
		self.assertEqual(self._count(), 1)

	def test_retries_a_deadlock(self):
		bulk_insert = frappe.db.bulk_insert
		attempts = []

		def deadlock_once(*args, **kwargs):
			attempts.append(1)
			if len(attempts) == 1:
				raise OperationalError(ER.LOCK_DEADLOCK, "Deadlock found when trying to get lock")
			return bulk_insert(*args, **kwargs)

		with (
			patch.object(frappe.db, "bulk_insert", side_effect=deadlock_once),
			patch("pulse.pulse.doctype.pulse_event.pulse_event.time.sleep"),
		):
			result = _bulk_ingest([self._event("one")], browser_direct=False)

		self.assertEqual(result["accepted"], 1)
		self.assertEqual(len(attempts), 2)
		self.assertEqual(self._count(), 1)
		self.assertEqual(self._reported()["retries"], 1)

	def test_storage_failure_is_a_503_and_stores_nothing(self):
		# Unlike a rejected event, this one the client *should* retry — so it must
		# surface as a failed request rather than a per-event rejection.
		bulk_insert = frappe.db.bulk_insert

		def insert_then_fail(*args, **kwargs):
			bulk_insert(*args, **kwargs)
			raise OperationalError(2013, "Lost connection to server during query")

		with patch.object(frappe.db, "bulk_insert", side_effect=insert_then_fail):
			with self.assertRaises(StorageUnavailable) as raised:
				_bulk_ingest([self._event("one"), self._event("two")], browser_direct=False)

		self.assertEqual(raised.exception.http_status_code, 503)
		self.assertEqual(self._count(), 0)
		reported = self._reported()
		self.assertTrue(reported["failed"])
		self.assertEqual(reported["accepted"], 0)

	def test_redis_stream_mode_stages_to_the_stream(self):
		self._set_ingest_mode("Redis Stream")

		result = _bulk_ingest([self._event("one"), self._event("two")], browser_direct=False)

		self.assertEqual(result["accepted"], 2)
		self.assertEqual(self.stream.get_length(), 2)
		self.assertEqual(self._count(), 0)
		self.assertIsNone(self._reported()["insert_ms"])

	def test_single_event_ingest_stores_the_event(self):
		ingest(**self._event("single"))

		self.assertEqual(self._count(), 1)
		self.assertEqual(self._reported()["accepted"], 1)

	def test_single_event_ingest_raises_on_a_bad_event(self):
		with self.assertRaises(frappe.ValidationError):
			ingest(event_name="Not A Name", captured_at=str(frappe.utils.now_datetime()))

		self.assertEqual(self._reported()["rejected"], 1)
