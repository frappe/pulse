app_name = "pulse"
app_title = "Pulse"
app_publisher = "hello@frappe.io"
app_description = "A Frappe app to track user interactions with your product."
app_email = "hello@frappe.io"
app_license = "AGPLv3"

# Apps
# ------------------

# required_apps = []

# Shown on the apps screen. Pulse has no frontend of its own — the route is its
# Desk workspace, which is where every doctype it owns is reached from.
add_to_apps_screen = [
	{
		"name": "pulse",
		"logo": "/assets/pulse/images/logo.svg",
		"title": "Pulse",
		"route": "/desk/pulse",
		"has_permission": "pulse.api.has_app_permission",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pulse/css/pulse.css"
# app_include_js = "/assets/pulse/js/pulse.js"

# include js, css files in header of web template
# web_include_css = "/assets/pulse/css/pulse.css"
# web_include_js = "/assets/pulse/js/pulse.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pulse/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "pulse/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "pulse.utils.jinja_methods",
# 	"filters": "pulse.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pulse.install.before_install"
# after_install = "pulse.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pulse.uninstall.before_uninstall"
# after_uninstall = "pulse.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pulse.utils.before_app_install"
# after_app_install = "pulse.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pulse.utils.before_app_uninstall"
# after_app_uninstall = "pulse.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pulse.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": [
		"pulse.pulse.doctype.pulse_event.pulse_event.consume_pulse_events",
		"pulse.utils.simulator.run_scheduled_simulation",
	],
	"hourly": [
		"pulse.metrics.flush_ingest_stats",
	],
}

# Testing
# -------

# before_tests = "pulse.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pulse.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pulse.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pulse.utils.before_request"]
# after_request = ["pulse.utils.after_request"]

# Job Events
# ----------
# before_job = ["pulse.utils.before_job"]
# after_job = ["pulse.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"pulse.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
