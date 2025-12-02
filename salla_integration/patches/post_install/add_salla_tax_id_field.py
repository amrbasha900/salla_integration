import frappe

from salla_integration.setup.install import create_salla_custom_fields


def execute():
    """Ensure new Salla Tax ID custom field exists on sales tax templates."""
    create_salla_custom_fields()
    frappe.clear_cache(doctype="Custom Field")

