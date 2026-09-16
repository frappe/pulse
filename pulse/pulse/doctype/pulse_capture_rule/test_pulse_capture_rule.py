# Copyright (c) 2025, hello@frappe.io and Contributors
# See license.txt

"""Capture rules: what they match, and what an event does when one matches it."""

import uuid

import frappe
from frappe.tests import IntegrationTestCase

from pulse.capture import DROP, MARK_INTERNAL, clear_rule_cache, evaluate
from pulse.pulse.doctype.pulse_event.pulse_event import (
	consume_pulse_events,
	prepare_event,
	store_events,
)
from pulse.pulse.doctype.redis_stream.redis_stream import RedisStream


class IntegrationTestPulseCaptureRule(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		token = uuid.uuid4().hex
		self.prefix = f"test_{token}_"
		frappe.flags.test_stream_name = f"test_capture_rule_{token}"
		self.stream = RedisStream.init()
		self.rules = []
		# The stream path is what these tests drain; Direct mode would store on ingest.
		self.ingest_mode = frappe.db.get_single_value("Pulse Settings", "ingest_mode")
		frappe.db.set_single_value("Pulse Settings", "ingest_mode", "Redis Stream")

	def tearDown(self):
		for rule in self.rules:
			frappe.delete_doc("Pulse Capture Rule", rule, force=True, ignore_permissions=True)
		clear_rule_cache()
		frappe.db.set_single_value("Pulse Settings", "ingest_mode", self.ingest_mode)
		self.stream.delete()
		frappe.flags.test_stream_name = None
		frappe.db.delete("Pulse Event", {"event_name": ("like", f"{self.prefix}%")})
		frappe.db.commit()
		super().tearDown()

	def _rule(self, match_field, match_value, action=DROP, match_operator="Equals"):
		doc = frappe.get_doc(
			{
				"doctype": "Pulse Capture Rule",
				"match_field": match_field,
				"match_operator": match_operator,
				"match_value": match_value,
				"action": action,
			}
		).insert(ignore_permissions=True)
		self.rules.append(doc.name)
		return doc

	def test_no_rules_captures_everything(self):
		self.assertIsNone(evaluate(event_name="pageview", site="real.frappe.cloud"))

	def test_matches_an_exact_value(self):
		self._rule("Site", "ava-test-2.frappe.cloud")

		self.assertEqual(evaluate(site="ava-test-2.frappe.cloud"), DROP)
		self.assertIsNone(evaluate(site="real.frappe.cloud"))

	def test_matches_a_wildcard_pattern(self):
		self._rule("Site", "*.staging.frappe.cloud", match_operator="Matches")

		self.assertEqual(evaluate(site="acme.staging.frappe.cloud"), DROP)
		self.assertIsNone(evaluate(site="acme.frappe.cloud"))

	def test_a_rule_ignores_events_missing_the_field(self):
		self._rule("User", "load_tester")

		self.assertIsNone(evaluate(site="real.frappe.cloud"))

	def test_drop_wins_over_mark_internal(self):
		self._rule("App", "erpnext", action=MARK_INTERNAL)
		self._rule("Site", "ava-test-2.frappe.cloud", action=DROP)

		self.assertEqual(evaluate(site="ava-test-2.frappe.cloud", app="erpnext"), DROP)

	def test_a_rule_matching_everything_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._rule("Site", "*", match_operator="Matches")

	def test_dropped_event_never_reaches_the_stream(self):
		self._rule("Site", "noisy.frappe.cloud")

		event = prepare_event(
			event_name=f"{self.prefix}pageview",
			captured_at=frappe.utils.now_datetime(),
			site="noisy.frappe.cloud",
		)

		self.assertIsNone(event)
		self.assertEqual(self.stream.get_length(), 0)

	def test_internal_event_is_stored_and_flagged(self):
		self._rule("Site", "staging.frappe.cloud", action=MARK_INTERNAL)

		event = prepare_event(
			event_name=f"{self.prefix}pageview",
			captured_at=frappe.utils.now_datetime(),
			site="staging.frappe.cloud",
		)
		store_events([event])
		consume_pulse_events()

		row = frappe.get_all(
			"Pulse Event",
			filters={"event_name": f"{self.prefix}pageview"},
			fields=["is_internal"],
		)[0]
		self.assertEqual(row.is_internal, 1)

	def test_normal_event_is_not_flagged_internal(self):
		event = prepare_event(
			event_name=f"{self.prefix}pageview",
			captured_at=frappe.utils.now_datetime(),
			site="real.frappe.cloud",
		)
		store_events([event])
		consume_pulse_events()

		row = frappe.get_all(
			"Pulse Event",
			filters={"event_name": f"{self.prefix}pageview"},
			fields=["is_internal"],
		)[0]
		self.assertEqual(row.is_internal, 0)
