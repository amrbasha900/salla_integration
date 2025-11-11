# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe import _
from frappe.utils import now, getdate
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
    get_settings, should_auto_submit_orders
)
from salla_integration.api.customers import get_or_create_customer

def sync_order(store_name, order_data):
    """
    Sync a single order from Salla to ERPNext Sales Order
    
    Args:
        store_name: Name of Salla Store
        order_data: Order data from Salla API
    """
    salla_order_id = str(order_data.get('id'))
    
    # Check if order already exists
    existing_order = frappe.db.get_value(
        "Sales Order",
        {"salla_order_id": salla_order_id},
        "name"
    )
    
    if existing_order:
        frappe.logger().info(f"Order {salla_order_id} already exists as {existing_order}")
        return existing_order
    
    # Create new sales order
    return create_sales_order_from_salla(order_data, store_name)


def create_sales_order_from_salla(order_data, store_name):
    """
    Create Sales Order from Salla order data
    
    Args:
        order_data: Order data from Salla API
        store_name: Name of Salla Store
        
    Returns:
        Sales Order name
    """
    store = frappe.get_doc("Salla Store", store_name)
    settings = get_settings()
    
    # Get or create customer
    customer_data = order_data.get('customer', {})
    customer_name = get_or_create_customer(customer_data, store_name)
    
    # Prepare order date
    order_date = order_data.get('date')
    if order_date:
        order_date = getdate(order_date)
    else:
        order_date = getdate()
    
    # Calculate delivery date (order date + 7 days default)
    delivery_date = frappe.utils.add_days(order_date, 7)
    
    # Create sales order
    sales_order = frappe.get_doc({
        "doctype": "Sales Order",
        "customer": customer_name,
        "company": store.company,
        "transaction_date": order_date,
        "delivery_date": delivery_date,
        "po_no": order_data.get('reference_id', ''),
        # Salla fields
        "salla_is_from_salla": 1,
        "salla_store": store_name,
        "salla_order_id": str(order_data.get('id')),
        "salla_reference_id": order_data.get('reference_id', ''),
        "salla_sync_status": "Synced",
        "salla_last_synced": now(),
        "items": []
    })
    
    # Add order items
    for item_data in order_data.get('items', []):
        sales_order_item = prepare_sales_order_item(item_data, store_name)
        if sales_order_item:
            sales_order.append("items", sales_order_item)
    
    if not sales_order.items:
        frappe.throw(_("No valid items found in Salla order {0}").format(order_data.get('id')))
    
    # Set warehouse if configured
    if store.warehouse:
        for item in sales_order.items:
            item.warehouse = store.warehouse
    
    # Set price list if configured
    if store.price_list:
        sales_order.selling_price_list = store.price_list
    
    try:
        sales_order.insert(ignore_permissions=True)
        
        # Auto-submit if configured
        if should_auto_submit_orders():
            sales_order.submit()
        
        frappe.db.commit()
        
        frappe.logger().info(f"Created Sales Order: {sales_order.name} from Salla order: {order_data.get('id')}")
        
        # Send notification if enabled
        if settings.notify_on_new_orders and settings.notification_email:
            send_new_order_notification(sales_order)
        
        return sales_order.name
        
    except Exception as e:
        frappe.log_error(f"Failed to create sales order: {str(e)}", "Salla Order Creation")
        raise


def prepare_sales_order_item(item_data, store_name):
    """
    Prepare sales order item dict from Salla order item
    
    Args:
        item_data: Item data from Salla order
        store_name: Name of Salla Store
        
    Returns:
        Dict for sales order item or None if item not found
    """
    product_id = str(item_data.get('product_id'))
    
    # Find ERPNext item by Salla product ID
    item_code = frappe.db.get_value(
        "Item",
        {"salla_product_id": product_id, "salla_store": store_name},
        "item_code"
    )
    
    if not item_code:
        frappe.log_error(
            f"Item not found for Salla product ID: {product_id}",
            "Salla Order Item Mapping"
        )
        return None
    
    return {
        "item_code": item_code,
        "item_name": item_data.get('name', ''),
        "qty": item_data.get('quantity', 1),
        "rate": item_data.get('price', 0),
        "description": item_data.get('notes', ''),
        "salla_is_from_salla": 1,
        "salla_product_id": product_id
    }


def send_new_order_notification(sales_order):
    """Send email notification for new order"""
    settings = get_settings()
    
    try:
        frappe.sendmail(
            recipients=[settings.notification_email],
            subject=f"New Salla Order: {sales_order.name}",
            message=f"""
                <h3>New Order Received from Salla</h3>
                <p><strong>Sales Order:</strong> {sales_order.name}</p>
                <p><strong>Customer:</strong> {sales_order.customer}</p>
                <p><strong>Total Amount:</strong> {frappe.utils.fmt_money(sales_order.grand_total, currency=sales_order.currency)}</p>
                <p><strong>Salla Order ID:</strong> {sales_order.salla_order_id}</p>
                <p><a href="{frappe.utils.get_url_to_form('Sales Order', sales_order.name)}">View Order</a></p>
            """,
            delayed=False
        )
    except Exception as e:
        frappe.log_error(f"Failed to send order notification: {str(e)}", "Salla Order Notification")


# Document Events (called from hooks.py)

def on_sales_order_submit(doc, method):
    """Called when Sales Order is submitted"""
    if not doc.salla_order_id or not doc.salla_store:
        return
    
    settings = get_settings()
    if not settings.update_order_status_to_salla:
        return
    
    # Queue background job to update Salla
    frappe.enqueue(
        method="salla_integration.api.orders.update_order_status_in_salla",
        queue="short",
        sales_order_name=doc.name,
        status="processing",
        is_async=True
    )


def on_sales_order_cancel(doc, method):
    """Called when Sales Order is cancelled"""
    if not doc.salla_order_id or not doc.salla_store:
        return
    
    settings = get_settings()
    if not settings.update_order_status_to_salla:
        return
    
    # Queue background job to update Salla
    frappe.enqueue(
        method="salla_integration.api.orders.update_order_status_in_salla",
        queue="short",
        sales_order_name=doc.name,
        status="cancelled",
        is_async=True
    )


def update_order_status_in_salla(sales_order_name, status):
    """
    Update order status in Salla
    
    Args:
        sales_order_name: Name of Sales Order
        status: New status (processing, shipped, completed, cancelled)
    """
    from salla_integration.utils.salla_client import SallaClient
    
    sales_order = frappe.get_doc("Sales Order", sales_order_name)
    
    if not sales_order.salla_order_id or not sales_order.salla_store:
        return
    
    try:
        client = SallaClient(sales_order.salla_store)
        
        client.update_order_status(
            order_id=sales_order.salla_order_id,
            status=status
        )
        
        sales_order.salla_sync_status = "Synced"
        sales_order.salla_last_synced = now()
        sales_order.save(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(f"Updated order status in Salla: {sales_order.salla_order_id} to {status}")
        
    except Exception as e:
        sales_order.salla_sync_status = "Failed"
        sales_order.save(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.log_error(
            f"Failed to update order status in Salla: {str(e)}",
            "Salla Order Status Update"
        )

def on_sales_order_update(doc, method):
    """Called when Sales Order is updated after submit"""
    if not doc.salla_order_id or not doc.salla_store:
        return
    
    settings = get_settings()
    if not settings.update_order_status_to_salla:
        return
    
    # Determine status based on document state
    status = "processing"
    if doc.status == "Completed":
        status = "completed"
    elif doc.status == "Closed":
        status = "cancelled"
    
    # Queue background job to update Salla
    frappe.enqueue(
        method="salla_integration.api.orders.update_order_status_in_salla",
        queue="short",
        sales_order_name=doc.name,
        status=status,
        is_async=True
    )


@frappe.whitelist()
def refresh_order_from_salla(sales_order_name):
    """
    Fetch latest order details from Salla and compare
    Called from sales_order.js
    
    Args:
        sales_order_name: Name of Sales Order
        
    Returns:
        Dict with success status and changes
    """
    try:
        from salla_integration.utils.salla_client import SallaClient
        
        so = frappe.get_doc("Sales Order", sales_order_name)
        
        if not so.salla_order_id or not so.salla_store:
            return {
                "success": False,
                "error": "Sales Order is not linked to Salla"
            }
        
        # Get order from Salla
        client = SallaClient(so.salla_store)
        order_data = client.get_order(so.salla_order_id)
        
        if not order_data:
            return {
                "success": False,
                "error": "Order not found in Salla"
            }
        
        # Compare and detect changes
        changes = []
        
        # Check status
        salla_status = order_data.get('status', {}).get('name', '')
        if salla_status and salla_status != so.status:
            changes.append(f"Status changed in Salla: {salla_status}")
        
        # Check total
        salla_total = order_data.get('total', {}).get('amount', 0)
        if salla_total and abs(float(salla_total) - float(so.grand_total)) > 0.01:
            changes.append(f"Total amount differs: Salla={salla_total}, ERPNext={so.grand_total}")
        
        # Update last synced
        so.salla_last_synced = now()
        so.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "success": True,
            "changes": "\n".join(changes) if changes else "No changes detected",
            "has_changes": len(changes) > 0
        }
        
    except Exception as e:
        frappe.log_error(f"Failed to refresh order from Salla: {str(e)}", "Salla Order Refresh")
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def bulk_sync_orders(sales_order_names):
    """
    Bulk sync multiple orders to Salla
    Called from sales_order_list.js
    
    Args:
        sales_order_names: List of Sales Order names (JSON string or list)
        
    Returns:
        Dict with success count
    """
    import json
    
    try:
        # Handle JSON string input
        if isinstance(sales_order_names, str):
            sales_order_names = json.loads(sales_order_names)
        
        success_count = 0
        failed_count = 0
        
        for so_name in sales_order_names:
            try:
                so = frappe.get_doc("Sales Order", so_name)
                
                if not so.salla_order_id or not so.salla_store:
                    failed_count += 1
                    continue
                
                # Update status in Salla
                status = "processing"
                if so.docstatus == 1:
                    if so.status == "Completed":
                        status = "completed"
                    elif so.status == "Closed":
                        status = "cancelled"
                
                update_order_status_in_salla(so_name, status)
                success_count += 1
                
            except Exception as e:
                frappe.log_error(
                    f"Failed to sync order {so_name}: {str(e)}",
                    "Bulk Order Sync"
                )
                failed_count += 1
        
        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(sales_order_names)
        }
        
    except Exception as e:
        frappe.log_error(f"Bulk sync failed: {str(e)}", "Bulk Order Sync")
        return {
            "success": 0,
            "failed": len(sales_order_names) if isinstance(sales_order_names, list) else 0,
            "error": str(e)
        }

def sync_order(store_name, order_data):
    """
    Sync a single order from Salla to ERPNext Sales Order
    
    Args:
        store_name: Name of Salla Store
        order_data: Order data from Salla API
    """
    salla_order_id = str(order_data.get('id'))
    
    # Check if order already exists
    existing_order = frappe.db.get_value(
        "Sales Order",
        {"salla_order_id": salla_order_id},
        "name"
    )
    
    if existing_order:
        frappe.logger().info(f"Order {salla_order_id} already exists as {existing_order}")
        return existing_order
    
    # Create new sales order
    return create_sales_order_from_salla(order_data, store_name)


def create_sales_order_from_salla(order_data, store_name):
    """
    Create Sales Order from Salla order data
    
    Args:
        order_data: Order data from Salla API
        store_name: Name of Salla Store
        
    Returns:
        Sales Order name
    """
    store = frappe.get_doc("Salla Store", store_name)
    settings = get_settings()
    
    # Get or create customer
    customer_data = order_data.get('customer', {})
    customer_name = get_or_create_customer(customer_data, store_name)
    
    # Prepare order date
    order_date = order_data.get('date')
    if order_date:
        order_date = getdate(order_date)
    else:
        order_date = getdate()
    
    # Calculate delivery date (order date + 7 days default)
    delivery_date = frappe.utils.add_days(order_date, 7)
    
    # Create sales order
    sales_order = frappe.get_doc({
        "doctype": "Sales Order",
        "customer": customer_name,
        "company": store.company,
        "transaction_date": order_date,
        "delivery_date": delivery_date,
        "po_no": order_data.get('reference_id', ''),
        # Salla fields
        "salla_is_from_salla": 1,
        "salla_store": store_name,
        "salla_order_id": str(order_data.get('id')),
        "salla_reference_id": order_data.get('reference_id', ''),
        "salla_sync_status": "Synced",
        "salla_last_synced": now(),
        "items": []
    })
    
    # Add order items
    for item_data in order_data.get('items', []):
        sales_order_item = prepare_sales_order_item(item_data, store_name)
        if sales_order_item:
            sales_order.append("items", sales_order_item)
    
    if not sales_order.items:
        frappe.throw(_("No valid items found in Salla order {0}").format(order_data.get('id')))
    
    # Set warehouse if configured
    if store.warehouse:
        for item in sales_order.items:
            item.warehouse = store.warehouse
    
    # Set price list if configured
    if store.price_list:
        sales_order.selling_price_list = store.price_list
    
    try:
        sales_order.insert(ignore_permissions=True)
        
        # Auto-submit if configured
        if should_auto_submit_orders():
            sales_order.submit()
        
        frappe.db.commit()
        
        frappe.logger().info(f"Created Sales Order: {sales_order.name} from Salla order: {order_data.get('id')}")
        
        # Send notification if enabled
        if settings.notify_on_new_orders and settings.notification_email:
            send_new_order_notification(sales_order)
        
        return sales_order.name
        
    except Exception as e:
        frappe.log_error(f"Failed to create sales order: {str(e)}", "Salla Order Creation")
        raise


def prepare_sales_order_item(item_data, store_name):
    """
    Prepare sales order item dict from Salla order item
    
    Args:
        item_data: Item data from Salla order
        store_name: Name of Salla Store
        
    Returns:
        Dict for sales order item or None if item not found
    """
    product_id = str(item_data.get('product_id'))
    
    # Find ERPNext item by Salla product ID
    item_code = frappe.db.get_value(
        "Item",
        {"salla_product_id": product_id, "salla_store": store_name},
        "item_code"
    )
    
    if not item_code:
        frappe.log_error(
            f"Item not found for Salla product ID: {product_id}",
            "Salla Order Item Mapping"
        )
        return None
    
    return {
        "item_code": item_code,
        "item_name": item_data.get('name', ''),
        "qty": item_data.get('quantity', 1),
        "rate": item_data.get('price', 0),
        "description": item_data.get('notes', ''),
        "salla_is_from_salla": 1,
        "salla_product_id": product_id
    }


def send_new_order_notification(sales_order):
    """Send email notification for new order"""
    settings = get_settings()
    
    try:
        frappe.sendmail(
            recipients=[settings.notification_email],
            subject=f"New Salla Order: {sales_order.name}",
            message=f"""
                <h3>New Order Received from Salla</h3>
                <p><strong>Sales Order:</strong> {sales_order.name}</p>
                <p><strong>Customer:</strong> {sales_order.customer}</p>
                <p><strong>Total Amount:</strong> {frappe.utils.fmt_money(sales_order.grand_total, currency=sales_order.currency)}</p>
                <p><strong>Salla Order ID:</strong> {sales_order.salla_order_id}</p>
                <p><a href="{frappe.utils.get_url_to_form('Sales Order', sales_order.name)}">View Order</a></p>
            """,
            delayed=False
        )
    except Exception as e:
        frappe.log_error(f"Failed to send order notification: {str(e)}", "Salla Order Notification")


# Document Events (called from hooks.py)

def on_sales_order_submit(doc, method):
    """Called when Sales Order is submitted"""
    if not doc.salla_order_id or not doc.salla_store:
        return
    
    settings = get_settings()
    if not settings.update_order_status_to_salla:
        return
    
    # Queue background job to update Salla
    frappe.enqueue(
        method="salla_integration.api.orders.update_order_status_in_salla",
        queue="short",
        sales_order_name=doc.name,
        status="processing",
        is_async=True
    )


def on_sales_order_cancel(doc, method):
    """Called when Sales Order is cancelled"""
    if not doc.salla_order_id or not doc.salla_store:
        return
    
    settings = get_settings()
    if not settings.update_order_status_to_salla:
        return
    
    # Queue background job to update Salla
    frappe.enqueue(
        method="salla_integration.api.orders.update_order_status_in_salla",
        queue="short",
        sales_order_name=doc.name,
        status="cancelled",
        is_async=True
    )


def on_sales_order_update(doc, method):
    """Called when Sales Order is updated after submit"""
    if not doc.salla_order_id or not doc.salla_store:
        return
    
    settings = get_settings()
    if not settings.update_order_status_to_salla:
        return
    
    # Determine status based on document state
    status = "processing"
    if doc.status == "Completed":
        status = "completed"
    elif doc.status == "Closed":
        status = "cancelled"
    
    # Queue background job to update Salla
    frappe.enqueue(
        method="salla_integration.api.orders.update_order_status_in_salla",
        queue="short",
        sales_order_name=doc.name,
        status=status,
        is_async=True
    )


@frappe.whitelist()
def refresh_order_from_salla(sales_order_name):
    """
    Fetch latest order details from Salla and compare
    Called from sales_order.js
    
    Args:
        sales_order_name: Name of Sales Order
        
    Returns:
        Dict with success status and changes
    """
    try:
        from salla_integration.utils.salla_client import SallaClient
        
        so = frappe.get_doc("Sales Order", sales_order_name)
        
        if not so.salla_order_id or not so.salla_store:
            return {
                "success": False,
                "error": "Sales Order is not linked to Salla"
            }
        
        # Get order from Salla
        client = SallaClient(so.salla_store)
        order_data = client.get_order(so.salla_order_id)
        
        if not order_data:
            return {
                "success": False,
                "error": "Order not found in Salla"
            }
        
        # Compare and detect changes
        changes = []
        
        # Check status
        salla_status = order_data.get('status', {}).get('name', '')
        if salla_status and salla_status != so.status:
            changes.append(f"Status changed in Salla: {salla_status}")
        
        # Check total
        salla_total = order_data.get('total', {}).get('amount', 0)
        if salla_total and abs(float(salla_total) - float(so.grand_total)) > 0.01:
            changes.append(f"Total amount differs: Salla={salla_total}, ERPNext={so.grand_total}")
        
        # Update last synced
        so.salla_last_synced = now()
        so.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "success": True,
            "changes": "\n".join(changes) if changes else "No changes detected",
            "has_changes": len(changes) > 0
        }
        
    except Exception as e:
        frappe.log_error(f"Failed to refresh order from Salla: {str(e)}", "Salla Order Refresh")
        return {
            "success": False,
            "error": str(e)
        }


@frappe.whitelist()
def bulk_sync_orders(sales_order_names):
    """
    Bulk sync multiple orders to Salla
    Called from sales_order_list.js
    
    Args:
        sales_order_names: List of Sales Order names (JSON string or list)
        
    Returns:
        Dict with success count
    """
    import json
    
    try:
        # Handle JSON string input
        if isinstance(sales_order_names, str):
            sales_order_names = json.loads(sales_order_names)
        
        success_count = 0
        failed_count = 0
        
        for so_name in sales_order_names:
            try:
                so = frappe.get_doc("Sales Order", so_name)
                
                if not so.salla_order_id or not so.salla_store:
                    failed_count += 1
                    continue
                
                # Update status in Salla
                status = "processing"
                if so.docstatus == 1:
                    if so.status == "Completed":
                        status = "completed"
                    elif so.status == "Closed":
                        status = "cancelled"
                
                update_order_status_in_salla(so_name, status)
                success_count += 1
                
            except Exception as e:
                frappe.log_error(
                    f"Failed to sync order {so_name}: {str(e)}",
                    "Bulk Order Sync"
                )
                failed_count += 1
        
        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(sales_order_names)
        }
        
    except Exception as e:
        frappe.log_error(f"Bulk sync failed: {str(e)}", "Bulk Order Sync")
        return {
            "success": 0,
            "failed": len(sales_order_names) if isinstance(sales_order_names, list) else 0,
            "error": str(e)
        }


@frappe.whitelist()
def bulk_update_status(sales_order_names, status):
    """
    Bulk update status for multiple orders in Salla
    Called from sales_order_list.js
    
    Args:
        sales_order_names: List of Sales Order names (JSON string or list)
        status: New status (processing, shipped, completed, cancelled)
        
    Returns:
        Dict with success count
    """
    import json
    
    try:
        # Handle JSON string input
        if isinstance(sales_order_names, str):
            sales_order_names = json.loads(sales_order_names)
        
        success_count = 0
        failed_count = 0
        
        for so_name in sales_order_names:
            try:
                so = frappe.get_doc("Sales Order", so_name)
                
                if not so.salla_order_id or not so.salla_store:
                    failed_count += 1
                    continue
                
                update_order_status_in_salla(so_name, status)
                success_count += 1
                
            except Exception as e:
                frappe.log_error(
                    f"Failed to update status for {so_name}: {str(e)}",
                    "Bulk Status Update"
                )
                failed_count += 1
        
        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(sales_order_names)
        }
        
    except Exception as e:
        frappe.log_error(f"Bulk status update failed: {str(e)}", "Bulk Status Update")
        return {
            "success": 0,
            "failed": len(sales_order_names) if isinstance(sales_order_names, list) else 0,
            "error": str(e)
        }


@frappe.whitelist()
def bulk_refresh_orders(sales_order_names):
    """
    Bulk refresh multiple orders from Salla
    Called from sales_order_list.js
    
    Args:
        sales_order_names: List of Sales Order names (JSON string or list)
        
    Returns:
        Dict with success count
    """
    import json
    
    try:
        # Handle JSON string input
        if isinstance(sales_order_names, str):
            sales_order_names = json.loads(sales_order_names)
        
        success_count = 0
        failed_count = 0
        
        for so_name in sales_order_names:
            try:
                result = refresh_order_from_salla(so_name)
                
                if result.get("success"):
                    success_count += 1
                else:
                    failed_count += 1
                    
            except Exception as e:
                frappe.log_error(
                    f"Failed to refresh order {so_name}: {str(e)}",
                    "Bulk Order Refresh"
                )
                failed_count += 1
        
        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(sales_order_names)
        }
        
    except Exception as e:
        frappe.log_error(f"Bulk refresh failed: {str(e)}", "Bulk Order Refresh")
        return {
            "success": 0,
            "failed": len(sales_order_names) if isinstance(sales_order_names, list) else 0,
            "error": str(e)
        }


@frappe.whitelist()
def get_salla_order_stats():
    """
    Get statistics for Salla orders
    Called from sales_order_list.js
    
    Returns:
        Dict with order statistics
    """
    try:
        # Today's orders
        total_today = frappe.db.count("Sales Order", {
            "salla_order_id": ["!=", ""],
            "transaction_date": frappe.utils.today()
        })
        
        # Synced orders
        synced = frappe.db.count("Sales Order", {
            "salla_order_id": ["!=", ""],
            "salla_sync_status": "Synced"
        })
        
        # Failed syncs
        failed = frappe.db.count("Sales Order", {
            "salla_order_id": ["!=", ""],
            "salla_sync_status": "Failed"
        })
        
        # Pending sync
        pending = frappe.db.count("Sales Order", {
            "salla_order_id": ["!=", ""],
            "salla_sync_status": ["in", ["Not Synced", ""]]
        })
        
        return {
            "total_today": total_today,
            "synced": synced,
            "failed": failed,
            "pending": pending
        }
        
    except Exception as e:
        frappe.log_error(f"Failed to get order stats: {str(e)}", "Salla Order Stats")
        return {
            "total_today": 0,
            "synced": 0,
            "failed": 0,
            "pending": 0
        }