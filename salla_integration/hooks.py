app_name = "salla_integration"
app_title = "Salla Integration"
app_publisher = "Amr Basha"
app_description = "Professional ERPNext integration with Salla e-commerce platform"
app_email = "amrbasha9000@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["frappe", "erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "salla_integration",
# 		"logo": "/assets/salla_integration/logo.png",
# 		"title": "Salla Integration",
# 		"route": "/salla_integration",
# 		"has_permission": "salla_integration.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/salla_integration/css/salla_integration.css"
# app_include_js = "/assets/salla_integration/js/salla_integration.js"

# include js, css files in header of web template
# web_include_css = "/assets/salla_integration/css/salla_integration.css"
# web_include_js = "/assets/salla_integration/js/salla_integration.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "salla_integration/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}
doctype_js = {
    "Item": "public/js/item.js",
    "Sales Order": "public/js/sales_order.js"
}

doctype_list_js = {
    "Item": "public/js/item_list.js",
    "Sales Order": "public/js/sales_order_list.js"
}
# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "salla_integration/public/icons.svg"

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

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "salla_integration.utils.jinja_methods",
# 	"filters": "salla_integration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "salla_integration.install.before_install"
# after_install = "salla_integration.install.after_install"
after_install = "salla_integration.setup.install.after_install"
after_migrate = "salla_integration.setup.install.after_install"

# Uninstallation
# ------------
before_uninstall = "salla_integration.setup.install.before_uninstall"
# after_uninstall = "salla_integration.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "salla_integration.utils.before_app_install"
# after_app_install = "salla_integration.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "salla_integration.utils.before_app_uninstall"
# after_app_uninstall = "salla_integration.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "salla_integration.notifications.get_notification_config"

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

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
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
doc_events = {
    "Item": {
        "after_insert": "salla_integration.api.items.after_item_insert",
        "on_update": "salla_integration.api.items.on_item_update",
        "on_trash": "salla_integration.api.items.on_item_delete"
    },
    "Sales Order": {
        "on_submit": "salla_integration.api.orders.on_sales_order_submit",
        "on_cancel": "salla_integration.api.orders.on_sales_order_cancel",
        "on_update_after_submit": "salla_integration.api.orders.on_sales_order_update"
    },
    "Stock Ledger Entry": {
        "on_submit": "salla_integration.api.items.on_stock_update"
    }
}
# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"salla_integration.tasks.all"
# 	],
# 	"daily": [
# 		"salla_integration.tasks.daily"
# 	],
# 	"hourly": [
# 		"salla_integration.tasks.hourly"
# 	],
# 	"weekly": [
# 		"salla_integration.tasks.weekly"
# 	],
# 	"monthly": [
# 		"salla_integration.tasks.monthly"
# 	],
# }
scheduler_events = {
    # Cron jobs - specific times
    "cron": {
        # Refresh tokens every day at 2 AM
        "0 2 * * *": [
            "salla_integration.tasks.refresh_store_tokens"
        ],
        # Sync inventory every 15 minutes
        "*/15 * * * *": [
            "salla_integration.tasks.sync_inventory_to_salla"
        ]
    },
    
    # Run on all scheduler ticks (every 5 minutes by default)
    "all": [
        # "salla_integration.tasks.all"
    ],
    
    # Daily tasks
    "daily": [
        "salla_integration.tasks.sync_all_stores_daily",
        "salla_integration.tasks.cleanup_old_sync_logs"
    ],
    
    # Hourly tasks
    "hourly": [
        "salla_integration.tasks.check_failed_syncs"
    ],
    
    # Weekly tasks
    "weekly": [
        "salla_integration.tasks.generate_weekly_report"
    ],
    
    # Monthly tasks
    "monthly": [
        # "salla_integration.tasks.monthly"
    ]
}
# Testing
# -------

# before_tests = "salla_integration.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "salla_integration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "salla_integration.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["salla_integration.utils.before_request"]
# after_request = ["salla_integration.utils.after_request"]

# Job Events
# ----------
# before_job = ["salla_integration.utils.before_job"]
# after_job = ["salla_integration.utils.after_job"]

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
# 	"salla_integration.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
default_log_clearing_doctypes = {
    "Salla Sync Log": 30  # Keep logs for 30 days
}
