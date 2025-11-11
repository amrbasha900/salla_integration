# Copyright (c) 2025, Amr Basha and contributors
# For license information, please see license.txt



import frappe
from frappe.model.document import Document

class SallaIntegrationSettings(Document):
    def validate(self):
        """Validate settings"""
        if self.sync_interval_minutes < 1:
            frappe.throw("Sync interval must be at least 1 minute")
        
        if self.batch_size < 1 or self.batch_size > 100:
            frappe.throw("Batch size must be between 1 and 100")
        
        if self.notify_on_sync_failure and not self.notification_email:
            frappe.throw("Notification email is required when sync failure notifications are enabled")


def get_settings():
    """
    Helper function to get integration settings
    Returns default values if settings don't exist
    """
    if not frappe.db.exists("Salla Integration Settings", "Salla Integration Settings"):
        # Return default settings
        return frappe._dict({
            "enable_integration": 1,
            "enable_debug_logging": 0,
            "enable_webhooks": 0,
            "sync_interval_minutes": 30,
            "max_retry_attempts": 3,
            "retry_delay_seconds": 5,
            "batch_size": 50,
            "auto_create_items": 1,
            "update_item_price": 1,
            "update_item_stock": 1,
            "sync_product_images": 0,
            "auto_create_sales_orders": 1,
            "auto_submit_sales_orders": 0,
            "update_order_status_to_salla": 1,
            "create_delivery_note_on_ship": 0,
            "auto_create_customers": 1,
            "sync_stock_to_salla": 1,
            "stock_sync_interval_minutes": 15,
            "sync_price_to_salla": 1,
            "price_sync_interval_minutes": 30,
            "notify_on_sync_failure": 1,
            "notify_on_new_orders": 0,
            "rate_limit_per_minute": 60,
            "request_timeout_seconds": 30,
            "use_exponential_backoff": 1,
            "max_backoff_seconds": 300
        })
    
    return frappe.get_single("Salla Integration Settings")


def is_integration_enabled():
    """Check if integration is enabled"""
    settings = get_settings()
    return settings.enable_integration


def is_debug_enabled():
    """Check if debug logging is enabled"""
    settings = get_settings()
    return settings.enable_debug_logging


def should_auto_create_items():
    """Check if items should be auto-created"""
    settings = get_settings()
    return settings.auto_create_items


def should_auto_create_customers():
    """Check if customers should be auto-created"""
    settings = get_settings()
    return settings.auto_create_customers


def should_auto_submit_orders():
    """Check if sales orders should be auto-submitted"""
    settings = get_settings()
    return settings.auto_submit_sales_orders


def get_batch_size():
    """Get configured batch size"""
    settings = get_settings()
    return settings.batch_size or 50


def get_max_retries():
    """Get max retry attempts"""
    settings = get_settings()
    return settings.max_retry_attempts or 3