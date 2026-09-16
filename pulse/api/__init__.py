import time

import frappe
from frappe.rate_limiter import rate_limit

from pulse import metrics
from pulse.anon import derive_anon_user
from pulse.logger import get_logger
from pulse.pulse.doctype.pulse_event.pulse_event import StorageUnavailable, prepare_event, store_events

logger = get_logger()


def has_app_permission():
	"""Whether to offer Pulse on the apps screen (see `add_to_apps_screen`).

	Everything Pulse owns is System Manager-only, so a user who cannot read an event
	would arrive at a workspace of empty lists. Keyed on that permission rather than
	on the role, so a site that grants access differently is respected.
	"""
	return bool(frappe.has_permission("Pulse Event", "read"))


def get_rate_limit():
	# Max ingest requests allowed per minute, per site (see `key="site"` below).
	# Keyed on site rather than IP: many Frappe Cloud sites share one outbound IP,
	# so a single shared IP bucket would throttle them all.
	return frappe.get_single_value("Pulse Settings", "rate_limit") or 60


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="site", limit=get_rate_limit, seconds=60)
def ingest(event_name, captured_at, site=None, app=None, user=None, team=None, properties=None):
	start = time.monotonic()
	check_auth()
	accepted = dropped = rejected = 0
	stored = frappe._dict(retries=0, insert_ms=None)
	failed = False

	try:
		event = prepare_event(
			event_name=event_name,
			captured_at=captured_at,
			site=site,
			app=app,
			user=user,
			team=team,
			properties=properties,
		)
		if event:
			stored = store_events([event])
			accepted = 1
		else:
			dropped = 1
	except Exception as e:
		# A rejection is the event's own fault and is counted as such; anything else
		# is the service failing.
		if isinstance(e, frappe.ValidationError):
			rejected = 1
		if isinstance(e, StorageUnavailable):
			failed = True
			stored = frappe._dict(retries=e.retries, insert_ms=e.insert_ms)
		logger.error(
			{
				"request_ip": frappe.local.request_ip,
				"event": {"event_name": event_name, "site": site, "app": app, "user": user},
				"error": str(e),
			}
		)
		raise e
	finally:
		metrics.record_request(
			accepted=accepted,
			dropped=dropped,
			rejected=rejected,
			duration_ms=(time.monotonic() - start) * 1000,
			insert_ms=stored.insert_ms,
			retries=stored.retries,
			failed=failed,
		)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def bulk_ingest(events, site=None):
	# Browser sends events as a form field (JSON string); server-to-server sends a
	# JSON body (already a list). That transport shape *is* the browser-direct signal:
	# only the browser path derives an anonymous `user`, and unlike "which header holds
	# the key" it can't be confused by a server caller — s2s always sends a list.
	browser_direct = isinstance(events, str)
	if isinstance(events, str):
		events = frappe.parse_json(events)
	if not isinstance(events, list):
		frappe.throw("Events must be a list", frappe.ValidationError)

	# The rate limiter reads its bucket key from form_dict. Prefer a top-level
	# `site` sent by the client; until the client sends it, fall back to the batch
	# (single-site, since the client's event queue is site-namespaced).
	frappe.form_dict["site"] = site or (events[0].get("site") if events else None)
	return _bulk_ingest(events, browser_direct)


@rate_limit(key="site", limit=get_rate_limit, seconds=60)
def _bulk_ingest(events, browser_direct):
	"""Store a batch, reporting per-event rejections instead of failing the batch.

	A rejected event is the client's to fix, not to retry: failing the whole request
	made the client resend the batch, re-sending the events that had already been
	accepted. So a bad event is reported in the response and the batch still succeeds.
	A storage failure is *not* caught — the request fails with a 503, and the client's
	retry is then the correct response.
	"""
	start = time.monotonic()
	check_auth()
	accepted = dropped = 0
	rejected = []
	stored = frappe._dict(retries=0, insert_ms=None)
	failed = False

	try:
		_resolve_anonymous_users(events, browser_direct)
		prepared = []
		for index, event in enumerate(events):
			event = frappe._dict(event)
			try:
				prepared_event = prepare_event(
					event_name=event.event_name,
					captured_at=event.captured_at,
					site=event.site,
					app=event.app,
					user=event.user,
					team=event.team,
					properties=event.properties,
				)
			except frappe.ValidationError as e:
				rejected.append({"index": index, "event_name": event.event_name, "error": str(e)})
				continue
			if prepared_event:
				prepared.append(prepared_event)
			else:
				dropped += 1

		try:
			stored = store_events(prepared)
		except StorageUnavailable as e:
			failed = True
			stored = frappe._dict(retries=e.retries, insert_ms=e.insert_ms)
			raise
		accepted = len(prepared)
	finally:
		metrics.record_request(
			accepted=accepted,
			dropped=dropped,
			rejected=len(rejected),
			duration_ms=(time.monotonic() - start) * 1000,
			insert_ms=stored.insert_ms,
			retries=stored.retries,
			failed=failed,
		)

	if rejected:
		logger.error(
			{
				"request_ip": frappe.local.request_ip,
				"site": frappe.form_dict.get("site"),
				"rejected": rejected,
				"error": "Rejected some events",
			}
		)

	return {"accepted": accepted, "dropped": dropped, "rejected": rejected}


def _resolve_anonymous_users(events, browser_direct):
	"""Fill the anonymous `user` for browser-direct events that arrive without one.

	A cookieless client sends no `user`; we derive it from the request. Events that
	already carry one — a login's `user_…` or a `client`-mode minted `anon_…` — are
	left as-is, so stages 2-4 and the opt-out need no declaration. Server-to-server
	(not browser-direct) always supplies a `user`, so it returns early.
	"""
	if not browser_direct:
		return
	for event in events:
		# Skip events without a site: deriving would need one, and a siteless event
		# shouldn't fail the batch — leave its `user` empty.
		if not event.get("user") and event.get("site"):
			event["user"] = derive_anon_user(event["site"])


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=get_rate_limit, seconds=60)
def anon_id(site=None):
	"""Return this visitor's current-day cookieless id (for `getDistinctId()`).

	The client can't compute it (the salt is server-side). It matches what the ingest
	path derives for this visitor's events, so a forwarded `aid` stitches via `alias()`.
	Rate-limited per IP (the real browser here) to cap enumeration of the derive oracle.
	"""
	check_auth()
	return {"anon_id": derive_anon_user(site)}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def identify(team, properties=None):
	check_auth()
	try:
		upsert_team(team, properties)
	except Exception as e:
		logger.error(
			{
				"request_ip": frappe.local.request_ip,
				"identify": {"team": team},
				"error": str(e),
			}
		)
		raise e


@frappe.whitelist(allow_guest=True, methods=["POST"])
def alias(previous_id, team):
	check_auth()
	try:
		upsert_alias(previous_id, team)
	except Exception as e:
		logger.error(
			{
				"request_ip": frappe.local.request_ip,
				"alias": {"previous_id": previous_id, "team": team},
				"error": str(e),
			}
		)
		raise e


def _merge_properties(existing, incoming):
	"""Merge incoming attributes over existing (later values win, others kept)."""
	if isinstance(existing, str):
		existing = frappe.parse_json(existing)
	if isinstance(incoming, str):
		incoming = frappe.parse_json(incoming)
	return {**(existing or {}), **(incoming or {})}


def upsert_team(team, properties=None):
	"""Create or update a Pulse Team profile, merging incoming attributes.

	The profile is keyed on the account `team`. Profiles are mutable state (set on
	change), not append-only events — so a later `identify` updates the given keys
	and keeps the rest.
	"""
	if not team:
		frappe.throw("team is required", frappe.ValidationError)

	if frappe.db.exists("Pulse Team", team):
		doc = frappe.get_doc("Pulse Team", team)
		doc.properties = _merge_properties(doc.properties, properties)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{"doctype": "Pulse Team", "team": team, "properties": _merge_properties({}, properties)}
		)
		doc.insert(ignore_permissions=True)
	return doc


def upsert_alias(previous_id, team):
	"""Record that a previous (anonymous) id maps to a known account team.

	Stores the mapping; re-attribution of historical events is resolved downstream in
	Insights (events joined to this alias map at query time). Pulse never rewrites
	historical event rows.

	Merge guard: only an *anonymous* id may be merged into an identified one.
	The endpoint accepts the public ingest key, so without this a caller could
	re-point an established id or collapse two real identities. We therefore refuse
	when either side is already an identity — leaving alias as an append-only anon→team link.
	"""
	if not previous_id or not team:
		frappe.throw("previous_id and team are required", frappe.ValidationError)

	if previous_id == team:
		return

	existing = frappe.db.get_value("Pulse Alias", previous_id, "team")
	if existing:
		# Already linked: idempotent if to the same identity, refused otherwise
		# (can't re-point an established alias to a different one).
		if existing != team:
			_refuse_merge(previous_id, team, reason="previous_id already mapped to another identity")
		return

	# `previous_id` must be anonymous: if it's itself an identity (has a profile, or
	# is the target of another alias), merging it under `team` would collapse two
	# identified subjects.
	if frappe.db.exists("Pulse Team", previous_id) or frappe.db.exists("Pulse Alias", {"team": previous_id}):
		_refuse_merge(previous_id, team, reason="previous_id is already an identified team")
		return

	doc = frappe.get_doc({"doctype": "Pulse Alias", "previous_id": previous_id, "team": team})
	doc.insert(ignore_permissions=True)
	return doc


def _refuse_merge(previous_id, team, reason):
	logger.warning(
		{
			"request_ip": getattr(frappe.local, "request_ip", None),
			"refused_merge": {"previous_id": previous_id, "team": team},
			"reason": reason,
		}
	)


def check_auth():
	api_key = frappe.get_single("Pulse Settings").get_password("api_key", raise_exception=False)
	if not api_key:
		logger.error("Pulse API key is not configured")
		frappe.throw("Pulse API key is not configured", frappe.PermissionError)

	# Browser-direct ingest sends the key in the form body (so the request stays a
	# preflight-free "simple" CORS request); server-to-server callers send the
	# X-Pulse-API-Key header.
	req_api_key = frappe.request.headers.get("X-Pulse-API-Key") or frappe.form_dict.get("api_key")
	if not req_api_key:
		logger.error(
			{
				"request_ip": frappe.local.request_ip,
				"error": "API key is missing",
			}
		)
		frappe.throw("API key is missing", frappe.PermissionError)

	if req_api_key != api_key:
		logger.error(
			{
				"request_ip": frappe.local.request_ip,
				"error": "Invalid API key",
			}
		)
		frappe.throw("Invalid API key", frappe.PermissionError)
