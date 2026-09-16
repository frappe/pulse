# Copyright (c) 2025, hello@frappe.io and contributors
# For license information, please see license.txt

"""A running count of what ingest did with the events it was sent, and how long it took.

Ingest can decline an event in two ways — a capture rule drops it, or validation
rejects it — and both are invisible in the stored data, because the whole point is
that nothing gets stored. A rule one character too broad, or an SDK that starts
sending a malformed name, would silently cost traffic that nobody would think to
look for. These counters are what make that noticeable.

They also answer the question the event table cannot: *was anything sent at all?*
A three-day dip in June 2026 (~9k/day against a ~40k baseline) is plain in the
captured data after the fact, and went unnoticed while it happened.

Ingest inserts events inside the request, so request and insert timings, retries and
failed requests are what show that path nearing its limit.

Counting is best-effort and never on the critical path — a request is counted in one
Redis round trip, and a Redis hiccup costs a number, not an event. Counts accumulate
per hour of site time in Redis, and an hourly job copies each completed hour into
Pulse Ingest Stat. Redis alone is not enough: a site moving to a new bench on update
starts with an empty one, so history kept there reads zero. A row written per request
would put a hot-row update on the ingest path; this way an update costs at most the
current hour.
"""

import math

import frappe
from frappe.utils import add_to_date, now_datetime

COUNTERS = ("requests", "failed_requests", "accepted", "dropped", "rejected", "retries")

# Upper bounds in milliseconds; the last bucket catches everything slower.
DURATION_BUCKETS = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, math.inf)
DURATIONS = ("request", "insert")

# Long enough for the flush to be missed for a day and still catch up.
RETENTION_HOURS = 48

_KEY_PREFIX = "pulse:ingest"

# HINCRBY cannot keep a maximum, and ZADD GT needs Redis 6.2.
_SET_MAX = """
local current = tonumber(redis.call('HGET', KEYS[1], ARGV[1]))
if not current or tonumber(ARGV[2]) > current then
	redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
end
"""


def _hour(moment) -> str:
	return moment.strftime("%Y-%m-%d %H:00")


def _key(hour: str) -> str:
	# Namespaced by hand: the counters are plain integers, so they use raw Redis
	# commands rather than the cache helpers, which pickle what they store.
	return frappe.cache.make_key(f"{_KEY_PREFIX}:{hour}")


def _bucket_field(duration: str, bound: float) -> str:
	return f"{duration}_ms:{'inf' if bound == math.inf else bound}"


def record_request(
	accepted: int = 0,
	dropped: int = 0,
	rejected: int = 0,
	duration_ms: float = 0,
	insert_ms: float | None = None,
	retries: int = 0,
	failed: bool = False,
):
	"""Add one ingest request to the current hour's counters. Never raises."""
	try:
		key = _key(_hour(now_datetime()))
		counts = {
			"requests": 1,
			"failed_requests": int(failed),
			"accepted": accepted,
			"dropped": dropped,
			"rejected": rejected,
			"retries": retries,
		}
		durations = {"request": duration_ms}
		if insert_ms is not None:
			durations["insert"] = insert_ms

		pipe = frappe.cache.pipeline(transaction=False)
		for field, count in counts.items():
			if count:
				pipe.hincrby(key, field, int(count))
		for duration, ms in durations.items():
			bound = next(b for b in DURATION_BUCKETS if ms <= b)
			pipe.hincrby(key, _bucket_field(duration, bound), 1)
			pipe.eval(_SET_MAX, 1, key, f"{duration}_max_ms", float(ms))
		pipe.expire(key, RETENTION_HOURS * 60 * 60)
		pipe.execute()
	except Exception:
		# Losing a count is not worth failing an ingest that already succeeded.
		pass


def percentile(buckets: dict[float, int], fraction: float, maximum: float) -> float:
	"""Return the upper bound of the bucket holding the `fraction` rank.

	Capped at the observed maximum, which also stands in for the open-ended last bucket.
	"""
	total = sum(buckets.values())
	if not total:
		return 0
	rank = math.ceil(fraction * total)
	seen = 0
	for bound in DURATION_BUCKETS:
		seen += buckets.get(bound, 0)
		if seen >= rank:
			return min(bound, maximum)
	return maximum


def _to_row(hour: str, values: dict) -> dict | None:
	if not values:
		return None
	values = {k.decode() if isinstance(k, bytes) else k: v for k, v in values.items()}
	row = {"hour": f"{hour}:00", **{field: int(values.get(field) or 0) for field in COUNTERS}}
	for duration in DURATIONS:
		maximum = float(values.get(f"{duration}_max_ms") or 0)
		buckets = {b: int(values.get(_bucket_field(duration, b)) or 0) for b in DURATION_BUCKETS}
		row[f"{duration}_p50_ms"] = percentile(buckets, 0.5, maximum)
		row[f"{duration}_p95_ms"] = percentile(buckets, 0.95, maximum)
		row[f"{duration}_max_ms"] = maximum
	return row


def flush_ingest_stats():
	"""Write each completed hour still in Redis to Pulse Ingest Stat, once."""
	now = now_datetime()
	hours = [_hour(add_to_date(now, hours=-offset)) for offset in range(1, RETENTION_HOURS + 1)]

	pipe = frappe.cache.pipeline(transaction=False)
	for hour in hours:
		pipe.hgetall(_key(hour))

	for hour, values in zip(hours, pipe.execute(), strict=True):
		row = _to_row(hour, values)
		if not row or frappe.db.exists("Pulse Ingest Stat", hour):
			continue
		try:
			frappe.get_doc({"doctype": "Pulse Ingest Stat", **row}).insert(
				set_name=hour, ignore_permissions=True
			)
			frappe.db.commit()
		except frappe.DuplicateEntryError:
			# A concurrent run wrote this hour first; everything before it is committed.
			frappe.db.rollback()
