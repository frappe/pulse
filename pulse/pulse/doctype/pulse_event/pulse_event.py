# Copyright (c) 2025, hello@frappe.io and contributors
# For license information, please see license.txt

import time
from uuid import uuid7

import frappe
from frappe.model.document import Document
from frappe.utils import cint, now_datetime
from frappe.utils.synchronization import LockTimeoutError, filelock

from pulse.capture import DROP, MARK_INTERNAL, evaluate
from pulse.dedup import dedup_key
from pulse.logger import get_logger
from pulse.pulse.doctype.pulse_event_catalog.pulse_event_catalog import record_events
from pulse.pulse.doctype.redis_stream.redis_stream import RedisStream
from pulse.utils import log_error
from pulse.validation import resolve_captured_at, serialize_properties, validate_event_name

logger = get_logger()


_EVENT_STREAMS = {}


def _get_event_stream() -> RedisStream:
	# In tests, a unique stream name is injected per test via
	# frappe.flags.test_stream_name; bypass the cache so each test gets its
	# own stream instead of a stale one from an earlier test.
	if frappe.flags.test_stream_name:
		return RedisStream.init()

	site = getattr(frappe.local, "site", None) or "default"
	# Cache per-site to avoid returning the stream for another site
	if site not in _EVENT_STREAMS:
		_EVENT_STREAMS[site] = RedisStream.init()
	return _EVENT_STREAMS[site]


REQD_FIELDS = ["event_name", "captured_at"]

# Columns written for each event row. On the stream path `name` is the Redis stream
# entry id, which makes the insert idempotent: a redelivered entry collides on the
# primary key and is skipped (see `consume_pulse_events`). `dedup_key` extends that
# same protection out past the host, to an event sent twice. The receive time is not
# stored as its own column — it is the row's `creation`.
_INSERT_FIELDS = [
	"name",
	"dedup_key",
	"event_name",
	"captured_at",
	"site",
	"app",
	"user",
	"team",
	"is_internal",
	"properties",
	"creation",
	"modified",
	"owner",
	"modified_by",
]

# How many entries to pull from the stream per batch, and how long a single
# scheduled drain is allowed to run before yielding to the next tick.
CONSUME_BATCH_SIZE = 1000
CONSUME_TIME_BUDGET_SECONDS = 50

# Sleep before each retry of an insert that hit a deadlock or lock wait timeout; one
# first attempt plus these makes three attempts in all.
INSERT_RETRY_DELAYS = (0.1, 0.2)


class PulseEvent(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		app: DF.Data | None
		captured_at: DF.Datetime | None
		event_name: DF.Data | None
		properties: DF.JSON | None
		site: DF.Data | None
		team: DF.Data | None
		user: DF.Data | None
	# end: auto-generated types

	def validate(self):
		missing = [field for field in REQD_FIELDS if not getattr(self, field)]
		if missing:
			frappe.throw(f"Missing required fields: {', '.join(missing)}")


class StorageUnavailable(Exception):
	"""The events could not be committed to the database.

	Answered with a 503 so the client keeps the batch and sends it again, instead of
	treating the request as settled.
	"""

	http_status_code = 503

	def __init__(self, retries=0, insert_ms=None):
		super().__init__("Event storage is unavailable")
		self.retries = retries
		self.insert_ms = insert_ms


def prepare_event(event_name, captured_at, site=None, app=None, user=None, team=None, properties=None):
	"""Validate a single event and build the row to store for it.

	This is the single funnel every event passes through, whichever endpoint it
	arrived at, so it is where the shape checks in `pulse.validation` are applied.

	Returns None when a capture rule drops the event (see `pulse.capture`), which is
	not a failure and is not reported as one.
	"""
	missing = [
		field for field, value in (("event_name", event_name), ("captured_at", captured_at)) if not value
	]
	if missing:
		frappe.throw(f"Missing required fields: {', '.join(missing)}")

	validate_event_name(event_name)

	action = evaluate(event_name=event_name, site=site, app=app, user=user)
	if action == DROP:
		return None

	received_at = now_datetime()
	captured_at = resolve_captured_at(captured_at, received_at)
	# Serialize to JSON here so the value lands in the JSON column as valid JSON
	# (the stream stringifies every field with cstr).
	properties = serialize_properties(properties)

	return {
		"dedup_key": dedup_key(event_name, captured_at, site, app, user, team, properties),
		"event_name": event_name,
		"captured_at": captured_at,
		"site": site,
		"user": user,
		"team": team,
		"app": app,
		"is_internal": 1 if action == MARK_INTERNAL else 0,
		"properties": properties,
		"received_at": received_at,
	}


def store_events(events: list[dict]) -> frappe._dict:
	"""Store events built by `prepare_event`.

	In Direct mode the events are committed to the database before this returns, so a
	successful ingest response means the events are stored. In Redis Stream mode they
	are only staged, for `consume_pulse_events` to flush later — the legacy path, kept
	for rollback.

	Returns the number of `retries` and the `insert_ms` spent inserting (None when
	nothing was inserted). Raises `StorageUnavailable` if the events could not be
	committed.
	"""
	if not events:
		return frappe._dict(retries=0, insert_ms=None)

	if frappe.get_single_value("Pulse Settings", "ingest_mode") == "Redis Stream":
		stream = _get_event_stream()
		for event in events:
			stream.add(event)
		return frappe._dict(retries=0, insert_ms=None)

	return _insert_events(events)


def _insert_events(events):
	# A time-ordered name keeps inserts appending to the primary key index. The name
	# column is shared with rows named after stream entry ids, so it stays a string.
	rows = [_row(str(uuid7()), event, event["received_at"]) for event in events]
	retries = 0
	start = time.monotonic()
	while True:
		try:
			frappe.db.bulk_insert("Pulse Event", _INSERT_FIELDS, rows, ignore_duplicates=True)
			frappe.db.commit()
			break
		except Exception as e:
			frappe.db.rollback()
			if retries < len(INSERT_RETRY_DELAYS) and (
				frappe.db.is_deadlocked(e) or frappe.db.is_timedout(e)
			):
				time.sleep(INSERT_RETRY_DELAYS[retries])
				retries += 1
				continue
			insert_ms = (time.monotonic() - start) * 1000
			logger.error(
				{
					"message": "Failed to store events",
					"events": len(rows),
					"retries": retries,
					"error": frappe.get_traceback(with_context=True),
				}
			)
			raise StorageUnavailable(retries=retries, insert_ms=insert_ms) from e

	insert_ms = (time.monotonic() - start) * 1000
	# Bookkeeping, deliberately after the commit: it must not stand between an event
	# and being stored, and it is allowed to fail on its own.
	record_events(events)
	return frappe._dict(retries=retries, insert_ms=insert_ms)


def _row_from_entry(entry, fallback_ts):
	data = entry.get("data", {})
	# `creation` is the receive time (set at ingest), not the flush time — the row
	# represents the moment the event was received.
	return _row(entry.get("id"), data, data.get("received_at") or fallback_ts)


def _row(name, data, received_at):
	return (
		name,
		# NULL rather than "" for an entry staged before this column existed: the
		# unique index tolerates any number of NULLs but only one "".
		data.get("dedup_key") or None,
		data.get("event_name"),
		data.get("captured_at"),
		data.get("site"),
		data.get("app"),
		data.get("user"),
		data.get("team"),
		cint(data.get("is_internal")),
		data.get("properties") or "{}",
		received_at,  # creation
		received_at,  # modified
		"Administrator",  # owner
		"Administrator",  # modified_by
	)


@log_error()
def consume_pulse_events():
	"""Drain the Redis staging stream into the `Pulse Event` table.

	Runs on the scheduler. Loops reading batches and bulk-inserting them until the
	stream is empty or the time budget is hit, so a backlog catches up across ticks
	instead of bleeding off one batch per minute. Inserts use `ignore_duplicates`
	keyed on the stream entry id, so the at-least-once delivery of Redis consumer
	groups (pending/stale redelivery) never produces duplicate rows.
	"""
	lock_name = f"pulse_consume_{getattr(frappe.local, 'site', None) or 'default'}"
	try:
		with filelock(lock_name, timeout=1):
			_consume_locked()
	except LockTimeoutError:
		# Another drain is already running; this tick is a no-op.
		return


def _consume_locked():
	stream = _get_event_stream()
	deadline = time.monotonic() + CONSUME_TIME_BUDGET_SECONDS

	while time.monotonic() < deadline:
		entries = stream.read(count=CONSUME_BATCH_SIZE)
		if not entries:
			break

		fallback_ts = now_datetime()
		rows = [_row_from_entry(entry, fallback_ts) for entry in entries]
		frappe.db.bulk_insert("Pulse Event", _INSERT_FIELDS, rows, ignore_duplicates=True)
		frappe.db.commit()

		# Ack only after the rows are durably committed. If we crash before this,
		# the entries stay pending and get redelivered, but the idempotent insert
		# makes the redelivery harmless.
		stream.ack_entries([entry["id"] for entry in entries])

		# Bookkeeping, deliberately last: it must not stand between an event and
		# being stored, and it is allowed to fail on its own.
		record_events([entry.get("data", {}) for entry in entries])

		if len(entries) < CONSUME_BATCH_SIZE:
			break
