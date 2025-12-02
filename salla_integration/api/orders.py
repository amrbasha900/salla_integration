# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe import _
from frappe.utils import now, getdate, flt
from typing import Any, Dict, List, Optional, Tuple
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
    get_settings, should_auto_submit_orders
)
from salla_integration.api.customers import get_or_create_customer
from salla_integration.api.variants import sync_salla_product
from salla_integration.utils.salla_client import SallaClient
import json

ALLOWED_TAX_FIELDS = [
    "charge_type",
    "account_head",
    "description",
    "included_in_print_rate",
    "included_in_paid_amount",
    "cost_center",
    "rate",
    "account_currency",
    "dont_recompute_tax",
]


def _is_variant_item(item_data: Dict[str, Any]) -> bool:
    """Detect whether order line represents a variant (template child) based on business rules."""
    options = item_data.get("options") or []
    if any(options):
        return True
    sku_id = item_data.get("product_sku_id")
    if sku_id in (None, "", 0):
        return False
    try:
        return int(sku_id) != 0
    except (TypeError, ValueError):
        return bool(sku_id)


def _get_template_sku(item_data: Dict[str, Any]) -> str:
    """Extract template (parent) SKU from line item if present."""
    product = item_data.get("product") or {}
    return _text(product.get("sku"))


def _text(value) -> str:
    """Return safe stripped string representation."""
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _extract_option_meta(item_data: Dict[str, Any]) -> Tuple[List[str], str]:
    """Return option value IDs and readable summary from order item payload."""
    option_value_ids: List[str] = []
    summary_parts: List[str] = []

    for option in (item_data or {}).get("options", []) or []:
        opt_name = _text(option.get("name") or option.get("label"))
        raw_values = option.get("value")
        values: List[Any]

        if raw_values in (None, ""):
            continue
        if isinstance(raw_values, list):
            values = raw_values
        else:
            values = [raw_values]

        for val in values:
            val_id = None
            label = ""
            if isinstance(val, dict):
                val_id = val.get("id")
                label = _text(val.get("name") or val.get("value") or val.get("display_value"))
            else:
                label = _text(val)

            if val_id is not None:
                option_value_ids.append(str(val_id))
                if not label:
                    label = str(val_id)

            if label:
                summary_parts.append(f"{opt_name}: {label}" if opt_name else label)

    summary = "\n".join(summary_parts)
    return option_value_ids, summary


def _load_option_values(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
        if isinstance(parsed, list):
            return [_text(v) for v in parsed if _text(v)]
    except Exception:
        pass
    if isinstance(raw_value, str):
        return [_text(v) for v in raw_value.split(",") if _text(v)]
    return []


def _find_variant_by_option_values(product_id: str, option_value_ids: List[str], store_name: str) -> Optional[str]:
    if not option_value_ids or not _item_option_field_available():
        return None
    target = {str(v) for v in option_value_ids if _text(v)}
    if not target:
        return None
    filters = {"salla_product_id": _text(product_id)}
    if store_name:
        filters["salla_store"] = store_name
    candidates = frappe.get_all(
        "Item",
        filters=filters,
        fields=["item_code", "salla_option_value_ids"]
    )
    if not candidates and store_name:
        candidates = frappe.get_all(
            "Item",
            filters={"salla_product_id": _text(product_id)},
            fields=["item_code", "salla_option_value_ids"]
        )
    for row in candidates or []:
        stored_ids = set(_load_option_values(row.get("salla_option_value_ids")))
        if stored_ids and stored_ids == target:
            return row.get("item_code")
    return None


def _sync_product_if_needed(product_id: str, store_name: str):
    product_id = _text(product_id)
    store_name = _text(store_name)
    if not product_id or not store_name:
        return
    cache = getattr(frappe.flags, "salla_products_synced_for_order", None)
    if not isinstance(cache, set):
        cache = set()
        frappe.flags.salla_products_synced_for_order = cache
    if product_id in cache:
        return
    try:
        client = SallaClient(store_name)
        product = client.get_product(product_id)
        if product:
            sync_salla_product(store_name, product, sync_log=None)
            cache.add(product_id)
            frappe.flags.salla_products_synced_for_order = cache
    except Exception as e:
        frappe.log_error(
            f"Failed to sync product {product_id} for store {store_name}: {e}",
            "Salla Order Item Auto Sync"
        )


def _get_item_doc(filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return frappe.db.get_value(
        "Item",
        filters,
        ["name", "has_variants", "variant_of"],
        as_dict=True,
    )


def _item_option_field_available() -> bool:
    try:
        return bool(frappe.db.has_column("tabItem", "salla_option_value_ids"))
    except Exception:
        return False


def _get_tax_template_by_percent(company: str, percent: float) -> Optional[str]:
    try:
        template_names = frappe.get_all(
            "Sales Taxes and Charges Template",
            filters={"company": company, "disabled": 0},
            pluck="name",
        )
    except Exception:
        return None
    for name in template_names:
        try:
            template = frappe.get_cached_doc("Sales Taxes and Charges Template", name)
        except Exception:
            continue
        for tax in template.taxes:
            try:
                tax_rate = float(tax.rate)
            except (TypeError, ValueError):
                continue
            if abs(tax_rate - percent) < 0.001:
                return template.name
    return None


def _apply_tax_template(sales_order, template_name: str):
    if not template_name:
        return
    try:
        template = frappe.get_cached_doc("Sales Taxes and Charges Template", template_name)
    except Exception:
        return
    sales_order.taxes_and_charges = template.name
    sales_order.set("taxes", [])
    for tax in template.taxes:
        row = {field: tax.get(field) for field in ALLOWED_TAX_FIELDS if field in tax.as_dict()}
        sales_order.append("taxes", row)


def _find_item_code(
    product_id: str,
    sku: str,
    store_name: str,
    option_value_ids: Optional[List[str]] = None,
    is_variant: bool = False,
) -> Optional[str]:
    """Resolve ERPNext Item code using SKU first, then product ID."""
    sku = _text(sku)
    product_id = _text(product_id)

    if sku:
        existing = _get_item_doc({"item_code": sku})
        if existing:
            is_template = bool(existing.get("has_variants")) or not existing.get("variant_of")
            if is_variant and is_template:
                frappe.log_error(
                    f"Variant SKU {sku} for product {product_id} points to template item {existing.get('name')}",
                    "Salla Order Item Mapping"
                )
            else:
                return existing.get("name")
        filters = {"salla_sku": sku}
        if store_name:
            filters["salla_store"] = store_name
        item_doc = _get_item_doc(filters)
        if not item_doc and store_name:
            # Fallback without store filter in case variant was synced before store tagging
            item_doc = _get_item_doc({"salla_sku": sku})
        if not item_doc:
            _sync_product_if_needed(product_id, store_name)
            item_doc = _get_item_doc(filters)
            if not item_doc and store_name:
                item_doc = _get_item_doc({"salla_sku": sku})
        if item_doc:
            is_template = bool(item_doc.get("has_variants")) or not item_doc.get("variant_of")
            if is_variant and is_template:
                frappe.log_error(
                    f"Variant SKU {sku} for product {product_id} points to template item {item_doc.get('name')}",
                    "Salla Order Item Mapping"
                )
            else:
                return item_doc.get("name")

    if is_variant:
        variant_code = _find_variant_by_option_values(product_id, option_value_ids or [], store_name)
        if variant_code:
            return variant_code
        _sync_product_if_needed(product_id, store_name)
        variant_code = _find_variant_by_option_values(product_id, option_value_ids or [], store_name)
        if variant_code:
            return variant_code
        frappe.log_error(
            f"Variant not found for product {product_id} with options {option_value_ids}",
            "Salla Order Item Variant Mapping"
        )
        return None

    if product_id:
        filters = {"salla_product_id": product_id}
        if store_name:
            filters["salla_store"] = store_name
        item_doc = _get_item_doc(filters)
        if not item_doc and store_name:
            item_doc = _get_item_doc({"salla_product_id": product_id})
        if not item_doc:
            _sync_product_if_needed(product_id, store_name)
            item_doc = _get_item_doc(filters)
            if not item_doc and store_name:
                item_doc = _get_item_doc({"salla_product_id": product_id})
        if item_doc:
            is_template = bool(item_doc.get("has_variants")) or not item_doc.get("variant_of")
            if is_variant and is_template:
                frappe.log_error(
                    f"Salla product {product_id} only has template item {item_doc.get('name')} for options {option_value_ids}",
                    "Salla Order Item Mapping"
                )
                return None
            return item_doc.get("name")

    return None


def check_taxes(store_name: str, order_id: str, sales_order) -> None:
    """
    Fetch order details from Salla to determine applicable tax template and apply it to the Sales Order.
    """
    if not store_name or not order_id:
        return
    try:
        client = SallaClient(store_name)
        detailed = client.get_order(order_id)
    except Exception:
        return

    if not isinstance(detailed, dict):
        return
    payload = detailed.get("data") if "data" in detailed else detailed
    if not isinstance(payload, dict):
        return

    amounts = payload.get("amounts")
    if not isinstance(amounts, dict):
        return
    tax_info = amounts.get("tax")
    if not isinstance(tax_info, dict):
        return

    percent = tax_info.get("percent")
    if percent in (None, ""):
        return
    try:
        percent_value = float(percent)
    except (TypeError, ValueError):
        return

    template_name = _get_tax_template_by_percent(sales_order.company, percent_value)
    if template_name:
        _apply_tax_template(sales_order, template_name)


def _get_item_rate(item_data: Dict[str, Any]) -> float:
    """Pick best available rate/price from Salla order item payload."""
    rate = item_data.get("price")
    if rate in (None, ""):
        rate = (
            ((item_data.get("amounts") or {}).get("price_without_tax") or {})
        ).get("amount")
    if rate in (None, ""):
        rate = (((item_data.get("amounts") or {}).get("total") or {}).get("amount"))
    return flt(rate or 0)

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

    status_data = order_data.get('status') or {}
    payment_data = order_data.get('payment') or {}
    delivery_method = order_data.get('delivery_method')
    
    order_id = _text(order_data.get('id'))

    items: List[Dict[str, Any]] = []
    client = None
    if order_id:
        try:
            client = SallaClient(store_name)
            items_response = client.get_order_items(order_id)
            items = items_response.get('data') or items_response.get('items') or []
        except Exception as e:
            frappe.log_error(
                f"Failed to fetch order items from Salla for order {order_id}: {e}",
                "Salla Order Item Fetch"
            )
    if not items:
        items = list(order_data.get('items') or [])
    order_data['items'] = items

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
        "salla_order_id": order_id,
        "salla_reference_id": order_data.get('reference_id', ''),
        "salla_status_id": _text(status_data.get('id')),
        "salla_status_slug": status_data.get('slug') or status_data.get('type'),
        "salla_status_name": status_data.get('name'),
        "salla_payment_status": payment_data.get('status'),
        "salla_payment_method": payment_data.get('method'),
        "salla_delivery_method": delivery_method,
        "salla_sync_status": "Synced",
        "salla_last_synced": now(),
        "items": []
    })

    check_taxes(store_name, order_id, sales_order)
    # Add order items
    for item_data in items:
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
    product_id = _text(item_data.get('product_id'))
    sku = _text(item_data.get('sku'))
    option_value_ids, option_summary = _extract_option_meta(item_data or {})
    is_variant = _is_variant_item(item_data or {})
    template_sku = _get_template_sku(item_data or {})

    if is_variant:
        if not sku:
            frappe.log_error(
                f"Variant SKU missing for Salla product ID: {product_id}, options: {option_value_ids}",
                "Salla Order Item Mapping"
            )
            return None
        if template_sku and sku == template_sku:
            frappe.log_error(
                f"Variant SKU {sku} matches template SKU for product {product_id}; skipping item",
                "Salla Order Item Mapping"
            )
            return None
    else:
        if not sku:
            frappe.log_error(
                f"Regular item missing SKU for Salla product ID: {product_id}",
                "Salla Order Item Mapping"
            )
            return None

    item_code = _find_item_code(
        product_id,
        sku,
        store_name,
        option_value_ids if is_variant else None,
        is_variant=is_variant,
    )
    
    if not item_code:
        frappe.log_error(
            f"Item not found for Salla product ID: {product_id}, SKU: {sku}, Store: {store_name}",
            "Salla Order Item Mapping"
        )
        return None

    qty = item_data.get('quantity', 1) or 1
    rate = _get_item_rate(item_data or {})
    sales_order_item = {
        "item_code": item_code,
        "qty": qty,
        "rate": rate,
        "description": item_data.get('notes', ''),
        "salla_is_from_salla": 1,
        "salla_product_id": product_id
    }

    if sku:
        sales_order_item["salla_item_sku"] = sku
    if option_value_ids:
        sales_order_item["salla_option_value_ids"] = ",".join(option_value_ids)
    if option_summary:
        sales_order_item["salla_option_summary"] = option_summary
    
    return sales_order_item


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
        
        status_data = order_data.get('status') or {}
        payment_data = order_data.get('payment') or {}

        # Compare and detect changes
        changes = []
        
        # Check status
        salla_status = status_data.get('name', '')
        if salla_status and salla_status != so.status:
            changes.append(f"Status changed in Salla: {salla_status}")
        
        # Check total
        salla_total = order_data.get('total', {}).get('amount', 0)
        if salla_total and abs(float(salla_total) - float(so.grand_total)) > 0.01:
            changes.append(f"Total amount differs: Salla={salla_total}, ERPNext={so.grand_total}")
        
        # Update Salla metadata snapshot
        so.salla_status_name = status_data.get('name')
        so.salla_status_slug = status_data.get('slug') or status_data.get('type')
        so.salla_status_id = _text(status_data.get('id'))
        so.salla_payment_status = payment_data.get('status')
        so.salla_payment_method = payment_data.get('method')
        if order_data.get('delivery_method'):
            so.salla_delivery_method = order_data.get('delivery_method')

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