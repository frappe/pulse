# Copyright (c) 2025, hello@frappe.io and Contributors
# See license.txt

"""Ingest counters: what they record per hour, how they reach the table, and that they never raise."""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from pulse import metrics

# Far from real traffic, so neither the counters nor the rows collide with it.
NOW = datetime(2001, 1, 1, 10, 30)
CURRENT_HOUR = "2001-01-01 10:00"
PAST_HOUR = "2001-01-01 09:00"
OLDER_HOUR = "2001-01-01 07:00"


class IntegrationTestMetrics(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.clock = patch("pulse.metrics.now_datetime", return_value=NOW)
		self.clock.start()
		self._clear()

	def tearDown(self):
		self._clear()
		self.clock.stop()
		super().tearDown()

	def _clear(self):
		hours = [metrics._hour(frappe.utils.add_to_date(NOW, hours=-h)) for h in range(0, 50)]
		frappe.cache.delete(*[metrics._key(hour) for hour in hours])
		frappe.db.delete("Pulse Ingest Stat", {"name": ("in", hours)})
		frappe.db.commit()

	def _record_at(self, moment, **kwargs):
		with patch("pulse.metrics.now_datetime", return_value=moment):
			metrics.record_request(**kwargs)

	def _row(self, hour):
		return metrics._to_row(hour, frappe.cache.pipeline().hgetall(metrics._key(hour)).execute()[0])

	def test_counts_a_request_in_its_hour(self):
		metrics.record_request(accepted=3, dropped=1, rejected=2, duration_ms=40, insert_ms=12, retries=1)
		metrics.record_request(accepted=1, duration_ms=700, failed=True)
		self._record_at(datetime(2001, 1, 1, 9, 59), accepted=5, duration_ms=5)

		row = self._row(CURRENT_HOUR)
		self.assertEqual(row["hour"], "2001-01-01 10:00:00")
		self.assertEqual(row["requests"], 2)
		self.assertEqual(row["failed_requests"], 1)
		self.assertEqual(row["accepted"], 4)
		self.assertEqual(row["dropped"], 1)
		self.assertEqual(row["rejected"], 2)
		self.assertEqual(row["retries"], 1)
		self.assertEqual(row["request_max_ms"], 700)
		self.assertEqual(row["insert_max_ms"], 12)
		self.assertEqual(self._row(PAST_HOUR)["accepted"], 5)

	def test_histogram_counts_each_duration_in_its_bucket(self):
		for ms in (3, 10, 11, 9000):
			metrics.record_request(duration_ms=ms)

		raw = frappe.cache.pipeline().hgetall(metrics._key(CURRENT_HOUR)).execute()[0]
		values = {k.decode(): int(float(v)) for k, v in raw.items()}
		self.assertEqual(values["request_ms:10"], 2)
		self.assertEqual(values["request_ms:25"], 1)
		self.assertEqual(values["request_ms:inf"], 1)
		self.assertEqual(values["request_max_ms"], 9000)
		self.assertNotIn("insert_ms:10", values)

	def test_percentile_is_the_bound_of_the_bucket_holding_the_rank(self):
		buckets = {10: 50, 100: 45, 1000: 5}
		self.assertEqual(metrics.percentile(buckets, 0.5, 900), 10)
		self.assertEqual(metrics.percentile(buckets, 0.95, 900), 100)
		self.assertEqual(metrics.percentile(buckets, 0.96, 900), 900)
		self.assertEqual(metrics.percentile({metrics.math.inf: 1}, 0.5, 7200), 7200)
		self.assertEqual(metrics.percentile({}, 0.5, 0), 0)

	def test_flush_writes_one_row_per_completed_hour(self):
		self._record_at(datetime(2001, 1, 1, 9, 5), accepted=2, duration_ms=30, insert_ms=8)
		self._record_at(datetime(2001, 1, 1, 9, 50), accepted=1, duration_ms=300, insert_ms=200)
		self._record_at(datetime(2001, 1, 1, 7, 0), rejected=1, duration_ms=1)
		metrics.record_request(accepted=9, duration_ms=1)

		metrics.flush_ingest_stats()

		row = frappe.get_doc("Pulse Ingest Stat", PAST_HOUR)
		self.assertEqual(str(row.hour), "2001-01-01 09:00:00")
		self.assertEqual((row.requests, row.accepted), (2, 3))
		self.assertEqual((row.request_p50_ms, row.request_p95_ms, row.request_max_ms), (50, 300, 300))
		self.assertEqual((row.insert_p50_ms, row.insert_p95_ms, row.insert_max_ms), (10, 200, 200))
		self.assertEqual(frappe.db.get_value("Pulse Ingest Stat", OLDER_HOUR, "rejected"), 1)
		self.assertFalse(frappe.db.exists("Pulse Ingest Stat", CURRENT_HOUR))
		self.assertFalse(frappe.db.exists("Pulse Ingest Stat", "2001-01-01 08:00"))

	def test_flush_is_idempotent(self):
		self._record_at(datetime(2001, 1, 1, 9, 5), accepted=2, duration_ms=30)
		metrics.flush_ingest_stats()
		self._record_at(datetime(2001, 1, 1, 9, 6), accepted=5, duration_ms=30)
		metrics.flush_ingest_stats()

		self.assertEqual(frappe.db.count("Pulse Ingest Stat", {"name": PAST_HOUR}), 1)
		self.assertEqual(frappe.db.get_value("Pulse Ingest Stat", PAST_HOUR, "accepted"), 2)

	def test_flush_tolerates_a_concurrent_run(self):
		self._record_at(datetime(2001, 1, 1, 9, 5), accepted=2, duration_ms=30)
		self._record_at(datetime(2001, 1, 1, 7, 5), accepted=4, duration_ms=30)

		# The other run inserts 09:00 between this run's existence check and its insert.
		with patch("frappe.db.exists", return_value=False):
			frappe.get_doc({"doctype": "Pulse Ingest Stat", **self._row(PAST_HOUR)}).insert(
				set_name=PAST_HOUR, ignore_permissions=True
			)
			frappe.db.commit()
			metrics.flush_ingest_stats()

		self.assertEqual(frappe.db.count("Pulse Ingest Stat", {"name": PAST_HOUR}), 1)
		self.assertEqual(frappe.db.get_value("Pulse Ingest Stat", OLDER_HOUR, "accepted"), 4)

	def test_record_request_swallows_redis_errors(self):
		with patch.object(frappe.cache, "pipeline", side_effect=RuntimeError("redis is down")):
			metrics.record_request(accepted=1, duration_ms=10)
		with patch("redis.client.Pipeline.execute", side_effect=RuntimeError("redis is down")):
			metrics.record_request(accepted=1, duration_ms=10)
