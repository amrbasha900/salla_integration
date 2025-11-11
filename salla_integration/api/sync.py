# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe import _
from salla_integration.utils.salla_client import SallaClient
from salla_integration.salla_integration.doctype.salla_sync_log.salla_sync_log import create_sync_log
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
    get_settings, is_integration_enabled
)
import traceback
from salla_integration.api.options import sync_product_options

@frappe.whitelist()
def start_sync(store_name):
    """
    Start full sync for a Salla store
    
    Args:
        store_name: Name of Salla Store
        
    Returns:
        Success status and sync log name
    """
    if not is_integration_enabled():
        frappe.throw(_("Salla Integration is disabled in settings"))
    
    try:
        # Create main sync log
        sync_log = create_sync_log(store_name, "Full Sync")
        
        # Queue background job for sync
        frappe.enqueue(
            method="salla_integration.api.sync.execute_full_sync",
            queue="long",
            timeout=3600,
            store_name=store_name,
            sync_log_name=sync_log.name
        )
        
        return {
            "success": True,
            "message": _("Sync job queued successfully"),
            "sync_log": sync_log.name
        }
        
    except Exception as e:
        frappe.log_error("Salla Sync Start", f"Failed to start sync: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


def execute_full_sync(store_name, sync_log_name):
    """
    Execute full sync in background
    This runs products, orders, customers sync
    """
    sync_log = frappe.get_doc("Salla Sync Log", sync_log_name)
    
    try:
        # Sync categories
        sync_store_categories(store_name)
        
        # Sync products options/attributes
        sync_store_options(store_name)
        
        # Sync products
        sync_store_products(store_name)
        
        # Sync customers
        sync_store_customers(store_name)
        
        # Sync orders
        sync_store_orders(store_name)
        
        sync_log.log_success()
        
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        sync_log.log_failure(error_msg, tb)
        
        # Send notification if enabled
        send_sync_failure_notification(store_name, error_msg)


@frappe.whitelist()
def start_options_sync(store_name):
    """
    Start only product options sync for a Salla store (enqueue)
    """
    if not is_integration_enabled():
        frappe.throw(_("Salla Integration is disabled in settings"))
    try:
        sync_log = create_sync_log(store_name, "Options")
        frappe.enqueue(
            method="salla_integration.api.sync.sync_store_options",
            queue="long",
            timeout=3600,
            store_name=store_name,
            sync_log_name=sync_log.name
        )
        return {
            "success": True,
            "message": _("Options sync job queued successfully"),
            "sync_log": sync_log.name
        }
    except Exception as e:
        frappe.log_error("Salla Options Sync Start", f"Failed to start options sync: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


def sync_store_options(store_name, sync_log_name=None):
    """
    Sync all product options from Salla to ERPNext (attributes/values + Salla Product Option doctypes)
    """
    if sync_log_name:
        sync_log = frappe.get_doc("Salla Sync Log", sync_log_name)
    else:
        sync_log = create_sync_log(store_name, "Options")
    try:
        client = SallaClient(store_name)
        settings = get_settings()
        total = 0
        batch_counter = 0
        any_failed = False
        error_lines = []
        for product in client.iterate_pages("get_products", per_page=settings.batch_size):
            total += 1
            batch_counter += 1
            try:
                result = sync_product_options(store_name, product, sync_log_name=sync_log.name)
                if result and result.get("errors"):
                    any_failed = True
                    error_lines.extend(result.get("errors") or [])
                sync_log.increment_success()
            except Exception as e:
                msg = f"Failed to sync options for product {product.get('id')}: {str(e)}"
                frappe.log_error("Salla Options Sync", msg)
                sync_log.increment_failed()
                any_failed = True
                error_lines.append(msg)
            if batch_counter >= 50:
                sync_log.total_records = total
                sync_log.save()
                frappe.db.commit()
                batch_counter = 0
        sync_log.total_records = total
        if error_lines:
            existing = sync_log.error_message or ""
            combined = (existing + ("\n" if existing else "") + "\n".join(error_lines)).strip()
            frappe.db.set_value("Salla Sync Log", sync_log.name, "error_message", combined)
            frappe.db.commit()
            sync_log.log_partial("Some option syncs failed. See Error Log and error_message.")
        else:
            sync_log.log_success()
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        sync_log.log_failure(error_msg, tb)
        raise


def sync_store_products(store_name):
    """
    Sync products from Salla to ERPNext
    
    Args:
        store_name: Name of Salla Store
    """
    from salla_integration.api.variants import sync_salla_product
    
    sync_log = create_sync_log(store_name, "Products")
    
    try:
        client = SallaClient(store_name)
        settings = get_settings()
        
        # Stream products per page to reduce memory
        total = 0
        batch_counter = 0
        for product in client.iterate_pages("get_products", per_page=settings.batch_size):
            total += 1
            batch_counter += 1
            try:
                sync_salla_product(store_name, product, sync_log=sync_log)
                sync_log.increment_success()
            except Exception as e:
                frappe.log_error(
                    "Salla Product Sync",
                    f"Failed to sync product {product.get('id')}: {str(e)}"
                )
                sync_log.increment_failed()
            # Commit periodically to keep transactions small
            if batch_counter >= 50:
                sync_log.total_records = total
                sync_log.save()
                frappe.db.commit()
                batch_counter = 0
        sync_log.total_records = total
        sync_log.save()
        sync_log.log_success()
        
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        sync_log.log_failure(error_msg, tb)
        raise


def sync_store_categories(store_name):
    """
    Sync categories from Salla and build the Salla Category tree
    
    Args:
        store_name: Name of Salla Store
    """
    from salla_integration.api.categories import sync_categories
    
    sync_log = create_sync_log(store_name, "Categories")
    
    try:
        result = sync_categories(store_name)
        if isinstance(result, dict) and result.get("total") is not None:
            sync_log.total_records = int(result.get("total") or 0)
            sync_log.save()
        sync_log.log_success()
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        sync_log.log_failure(error_msg, tb)
        raise


def sync_store_orders(store_name):
    """
    Sync orders from Salla to ERPNext
    
    Args:
        store_name: Name of Salla Store
    """
    from salla_integration.api.orders import sync_order
    
    sync_log = create_sync_log(store_name, "Orders")
    
    try:
        client = SallaClient(store_name)
        settings = get_settings()
        
        # Stream orders per page
        total = 0
        batch_counter = 0
        for order in client.iterate_pages("get_orders", per_page=settings.batch_size):
            total += 1
            batch_counter += 1
            try:
                # Check if order already exists
                if not frappe.db.exists("Sales Order", {"salla_order_id": order.get('id')}):
                    sync_order(store_name, order)
                    sync_log.increment_success()
                else:
                    sync_log.increment_skipped()
            except Exception as e:
                frappe.log_error(
                    "Salla Order Sync",
                    f"Failed to sync order {order.get('id')}: {str(e)}"
                )
                sync_log.increment_failed()
            if batch_counter >= 50:
                sync_log.total_records = total
                sync_log.save()
                frappe.db.commit()
                batch_counter = 0
        sync_log.total_records = total
        sync_log.save()
        sync_log.log_success()
        
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        sync_log.log_failure(error_msg, tb)
        raise


def sync_store_customers(store_name):
    """
    Sync customers from Salla to ERPNext
    
    Args:
        store_name: Name of Salla Store
    """
    from salla_integration.api.customers import sync_customer
    
    sync_log = create_sync_log(store_name, "Customers")
    
    try:
        client = SallaClient(store_name)
        settings = get_settings()
        
        # Stream customers per page
        total = 0
        batch_counter = 0
        for customer in client.iterate_pages("get_customers", per_page=settings.batch_size):
            total += 1
            batch_counter += 1
            try:
                sync_customer(store_name, customer)
                sync_log.increment_success()
            except Exception as e:
                frappe.log_error(
                    f"Failed to sync customer {customer.get('id')}: {str(e)}",
                    "Salla Customer Sync"
                )
                sync_log.increment_failed()
            if batch_counter >= 50:
                sync_log.total_records = total
                sync_log.save()
                frappe.db.commit()
                batch_counter = 0
        sync_log.total_records = total
        sync_log.save()
        sync_log.log_success()
        
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        sync_log.log_failure(error_msg, tb)
        raise


def push_inventory_updates(store_name):
    """
    Push inventory updates from ERPNext to Salla
    
    Args:
        store_name: Name of Salla Store
    """
    sync_log = create_sync_log(store_name, "Inventory")
    
    try:
        client = SallaClient(store_name)
        store = frappe.get_doc("Salla Store", store_name)
        
        # Get items that need sync
        items = frappe.get_all(
            "Item",
            filters={
                "salla_store": store_name,
                "salla_product_id": ["!=", ""]
            },
            fields=["name", "salla_product_id", "item_code"]
        )
        
        sync_log.total_records = len(items)
        sync_log.save()
        
        for item in items:
            try:
                # Get current stock quantity
                stock_qty = frappe.db.get_value(
                    "Bin",
                    {"item_code": item.item_code, "warehouse": store.warehouse},
                    "actual_qty"
                ) or 0
                
                # Update in Salla
                client.update_product_quantity(
                    product_id=item.salla_product_id,
                    quantity=int(stock_qty)
                )
                
                sync_log.increment_success()
                
            except Exception as e:
                frappe.log_error(
                    "Salla Inventory Sync",
                    f"Failed to update inventory for {item.item_code}: {str(e)}"
                )
                sync_log.increment_failed()
        
        sync_log.log_success()
        
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        sync_log.log_failure(error_msg, tb)


def send_sync_failure_notification(store_name, error_message):
    """Send email notification on sync failure"""
    settings = get_settings()
    
    if not settings.notify_on_sync_failure or not settings.notification_email:
        return
    
    try:
        frappe.sendmail(
            recipients=[settings.notification_email],
            subject=f"Salla Sync Failed: {store_name}",
            message=f"""
                <h3>Sync Failed for Store: {store_name}</h3>
                <p><strong>Error:</strong></p>
                <pre>{error_message}</pre>
                <p>Please check the Error Log and Salla Sync Log for more details.</p>
            """,
            delayed=False
        )
    except Exception as e:
        frappe.log_error("Salla Notification", f"Failed to send notification: {str(e)}")