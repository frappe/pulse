from datetime import timedelta

import frappe
from frappe.query_builder.functions import Min

SITE_DB = "Site DB"
EVENT_TABLE = "tabPulse Event"

# Past the hourly flush of Pulse Ingest Stat, so each import picks up the hour just closed.
SYNC_SCHEDULE = "15 * * * *"

TABLES = {
	# Reading events live times out at production volume. They are never updated,
	# so each import appends only the rows created since the last one.
	EVENT_TABLE: {
		"sync_mode": "Incremental",
		"sync_strategy": "Append Only",
		"sync_cursor_column": "creation",
	},
	"tabPulse Ingest Stat": {"sync_mode": "Full"},
	"tabPulse Event Catalog": {"sync_mode": "Full"},
}


def after_app_install(app: str):
	if app == "insights":
		configure_table_imports()


def configure_table_imports():
	"""Keep the tables the Pulse dashboards read in the Insights data store, imported hourly."""
	if "insights" not in frappe.get_installed_apps():
		return

	from insights.insights.doctype.insights_table_v3.insights_table_v3 import (
		InsightsTablev3,
		get_table_name,
	)

	InsightsTablev3.bulk_create(SITE_DB, list(TABLES))
	for name, settings in TABLES.items():
		table = frappe.get_doc("Insights Table v3", get_table_name(SITE_DB, name))
		table.update({**settings, "sync_schedule": SYNC_SCHEDULE})
		if name == EVENT_TABLE and not table.sync_from:
			event = frappe.qb.DocType("Pulse Event")
			first = frappe.qb.from_(event).select(Min(event.creation)).run()[0][0]
			# the first import reads rows created after `sync_from`, so start just before the oldest
			table.sync_from = (first or frappe.utils.now_datetime()) - timedelta(seconds=1)
		table.save(ignore_permissions=True)
