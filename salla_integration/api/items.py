# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe import _
from frappe.utils import now
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
    get_settings, should_auto_create_items
)
import hashlib
import json
from salla_integration.api.brands import upsert_brand
from salla_integration.api.options import sync_product_options

def sync_products(store_name, product_data):
    """
    Sync a single product from Salla to ERPNext Item
    
    Args:
        store_name: Name of Salla Store
        product_data: Product data from Salla API
    """
    if not should_auto_create_items():
        return
    frappe.log_error("Salla Product", f"Product data: {product_data}")
    if product_data.get("type") == "group_products":
        frappe.log_error("Salla Group Product", f"Group product detected: {product_data.get('id')}")
        return sync_group_product(store_name, product_data)
    
    salla_product_id = str(product_data.get('id'))
    
    # Check if item already exists
    existing_item = frappe.db.get_value(
        "Item",
        {"salla_product_id": salla_product_id, "salla_store": store_name},
        "name"
    )
    
    if existing_item:
        # Update existing item
        update_item_from_salla(existing_item, product_data, store_name)
    else:
        # Create new item
        create_item_from_salla(product_data, store_name)


def create_item_from_salla(product_data, store_name, is_bundle=False):
    """
    Create new Item from Salla product data
    
    Args:
        product_data: Product data from Salla API
        store_name: Name of Salla Store
    """
    store = frappe.get_doc("Salla Store", store_name)
    settings = get_settings()
    is_bundle = is_bundle or product_data.get("type") == "group_products"
    
    # Prepare item data
    item_code = product_data.get('sku') or f"SALLA-{product_data.get('id')}"
    item_name = product_data.get('name')
    
    # Get category (Item Group)
    item_group = get_or_create_item_group(
        product_data.get('category'),
        store_name
    ) if product_data.get('category') else "All Item Groups"
    
    # Get brand
    brand = get_or_create_brand(
        product_data.get('brand'),
        store_name
    ) if product_data.get('brand') else None
    
    # Create item
    item = frappe.get_doc({
        "doctype": "Item",
        "item_code": item_code,
        "item_name": item_name,
        "item_group": item_group,
        "stock_uom": "Nos",
        "is_stock_item": 0 if is_bundle else 1,
        "include_item_in_manufacturing": 0,
        "opening_stock": 0,
        "valuation_rate": 0,
        "standard_rate": product_data.get('price', 0),
        "description": product_data.get('description', ''),
        # Salla fields
        "salla_is_from_salla": 1,
        "salla_store": store_name,
        "salla_product_id": str(product_data.get('id')),
        "salla_sku": product_data.get('sku', ''),
        "salla_sync_hash": generate_sync_hash(product_data),
        "salla_last_synced": now()
    })
    
    if brand:
        item.brand = brand
    
    if store.warehouse:
        item.default_warehouse = store.warehouse
    
    try:
        item.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Create price list rate if configured
        if settings.update_item_price and store.price_list:
            create_item_price(item.name, product_data.get('price', 0), store.price_list)
        
        # Update stock if available
        if item.is_stock_item and settings.update_item_stock and product_data.get('quantity') and store.warehouse:
            update_item_stock(
                item.name,
                store.warehouse,
                product_data.get('quantity', 0)
            )
        
        # Sync product options (attributes/values) and log missing SKUs
        try:
            sync_product_options(store_name, product_data)
        except Exception as opt_err:
            frappe.log_error("Salla Option Sync", f"Option sync failed for product {product_data.get('id')}: {str(opt_err)}")
        
        frappe.logger().info(f"Created item: {item.name} from Salla product: {product_data.get('id')}")
        return item.name
        
    except Exception as e:
        frappe.log_error("Salla Item Creation", f"Failed to create item: {str(e)}")
        raise


def update_item_from_salla(item_name, product_data, store_name, is_bundle=False):
    """
    Update existing Item with Salla product data
    
    Args:
        item_name: ERPNext Item name
        product_data: Product data from Salla API
        store_name: Name of Salla Store
    """
    settings = get_settings()
    
    item = frappe.get_doc("Item", item_name)
    is_bundle = is_bundle or product_data.get("type") == "group_products"
    
    # Check if data has changed using hash
    new_hash = generate_sync_hash(product_data)
    if item.salla_sync_hash == new_hash and not is_bundle:
        # No changes, skip update
        return
    
    # Update item fields
    item.item_name = product_data.get('name')
    item.description = product_data.get('description', item.description)
    if is_bundle:
        item.is_stock_item = 0
    
    if settings.update_item_price:
        item.standard_rate = product_data.get('price', item.standard_rate)
    
    # Update category if changed
    if product_data.get('category'):
        item_group = get_or_create_item_group(product_data.get('category'), store_name)
        item.item_group = item_group
    
    # Update brand if changed
    if product_data.get('brand'):
        brand = get_or_create_brand(product_data.get('brand'), store_name)
        item.brand = brand
    
    item.salla_sync_hash = new_hash
    item.salla_last_synced = now()
    
    try:
        item.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Update price list
        store = frappe.get_doc("Salla Store", store_name)
        if settings.update_item_price and store.price_list:
            update_item_price(item.name, product_data.get('price', 0), store.price_list)
        
        # Update stock
        if item.is_stock_item and settings.update_item_stock and product_data.get('quantity') and store.warehouse:
            update_item_stock(
                item.name,
                store.warehouse,
                product_data.get('quantity', 0)
            )
        
        # Sync product options (attributes/values) and log missing SKUs
        try:
            sync_product_options(store_name, product_data)
        except Exception as opt_err:
            frappe.log_error("Salla Option Sync", f"Option sync failed for product {product_data.get('id')}: {str(opt_err)}")
        
        frappe.logger().info(f"Updated item: {item.name} from Salla product: {product_data.get('id')}")
        return item.name
        
    except Exception as e:
        frappe.log_error("Salla Item Update", f"Failed to update item: {str(e)}")
        raise


def get_or_create_item_group(category_data, store_name):
    """
    Get or create Item Group from Salla category
    
    Args:
        category_data: Category data from Salla
        store_name: Name of Salla Store
        
    Returns:
        Item Group name
    """
    if isinstance(category_data, dict):
        category_id = str(category_data.get('id'))
        category_name = category_data.get('name', f"Category {category_id}")
    else:
        category_id = str(category_data)
        category_name = f"Category {category_id}"
    
    # Check if already exists
    existing = frappe.db.get_value(
        "Item Group",
        {"salla_category_id": category_id, "salla_store": store_name},
        "name"
    )
    
    if existing:
        return existing
    
    # Create new item group
    item_group = frappe.get_doc({
        "doctype": "Item Group",
        "item_group_name": category_name,
        "parent_item_group": "All Item Groups",
        "is_group": 0,
        "salla_is_from_salla": 1,
        "salla_category_id": category_id,
        "salla_store": store_name
    })
    
    try:
        item_group.insert(ignore_permissions=True)
        frappe.db.commit()
        return item_group.name
    except Exception as e:
        frappe.log_error("Salla Item Group Creation", f"Failed to create item group: {str(e)}")
        return "All Item Groups"


def get_or_create_brand(brand_data, store_name):
    """
    Get or create Brand from Salla brand payload or ID.
    """
    if isinstance(brand_data, dict):
        return upsert_brand(store_name, brand_data)
    
    brand_id = str(brand_data)
    
    existing = frappe.db.get_value(
        "Brand",
        {"salla_brand_id": brand_id, "salla_store": store_name},
        "name"
    )
    
    if existing:
        return existing
    
    # Try fetching details from Salla for richer metadata
    try:
        from salla_integration.utils.salla_client import SallaClient
        client = SallaClient(store_name)
        response = client.get_brand(brand_id)
        brand_payload = response.get("data") if isinstance(response, dict) else response
        if isinstance(brand_payload, dict):
            return upsert_brand(store_name, brand_payload)
    except Exception:
        pass
    
    # Fallback: create placeholder brand with minimal info
    brand_name = f"Brand {brand_id}"
    brand = frappe.get_doc({
        "doctype": "Brand",
        "brand": brand_name,
        "salla_is_from_salla": 1,
        "salla_brand_id": brand_id,
        "salla_store": store_name
    })
    
    try:
        brand.insert(ignore_permissions=True)
        frappe.db.commit()
        return brand.name
    except Exception as e:
        frappe.log_error("Salla Brand Creation", f"Failed to create brand: {str(e)}")
        return None


def create_item_price(item_code, price, price_list):
    """Create or update Item Price"""
    existing_price = frappe.db.exists("Item Price", {
        "item_code": item_code,
        "price_list": price_list
    })
    
    if existing_price:
        update_item_price(item_code, price, price_list)
        return
    
    item_price = frappe.get_doc({
        "doctype": "Item Price",
        "item_code": item_code,
        "price_list": price_list,
        "price_list_rate": price
    })
    
    item_price.insert(ignore_permissions=True)
    frappe.db.commit()


def update_item_price(item_code, price, price_list):
    """Update existing Item Price"""
    frappe.db.set_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list},
        "price_list_rate",
        price
    )
    frappe.db.commit()


def update_item_stock(item_code, warehouse, quantity):
    """Update item stock using Stock Reconciliation"""
    # Skip if quantity is same
    current_qty = frappe.db.get_value(
        "Bin",
        {"item_code": item_code, "warehouse": warehouse},
        "actual_qty"
    ) or 0
    
    if current_qty == quantity:
        return
    
    # Create stock reconciliation
    stock_recon = frappe.get_doc({
        "doctype": "Stock Reconciliation",
        "purpose": "Stock Reconciliation",
        "posting_date": frappe.utils.today(),
        "posting_time": frappe.utils.nowtime(),
        "items": [{
            "item_code": item_code,
            "warehouse": warehouse,
            "qty": quantity,
            "valuation_rate": frappe.db.get_value("Item", item_code, "valuation_rate") or 0
        }]
    })
    
    try:
        stock_recon.insert(ignore_permissions=True)
        stock_recon.submit()
        frappe.db.commit()
    except Exception as e:
        frappe.log_error("Salla Stock Update", f"Failed to update stock for {item_code}: {str(e)}")


def generate_sync_hash(product_data):
    """Generate hash of product data to detect changes"""
    relevant_fields = {
        'name': product_data.get('name'),
        'price': product_data.get('price'),
        'description': product_data.get('description'),
        'sku': product_data.get('sku'),
        'quantity': product_data.get('quantity')
    }
    
    data_string = json.dumps(relevant_fields, sort_keys=True)
    return hashlib.md5(data_string.encode()).hexdigest()


# Document Events (called from hooks.py)

def after_item_insert(doc, method):
    """Called after Item is inserted"""
    pass


def on_item_update(doc, method):
    """Called when Item is updated - push changes to Salla if needed"""
    if not doc.salla_product_id or not doc.salla_store:
        return
    
    settings = get_settings()
    if not settings.sync_price_to_salla and not settings.sync_stock_to_salla:
        return
    
    # Queue background job to update Salla
    frappe.enqueue(
        method="salla_integration.api.items.push_item_to_salla",
        queue="short",
        item_name=doc.name,
        is_async=True
    )


def push_item_to_salla(item_name):
    """Push item updates to Salla"""
    from salla_integration.utils.salla_client import SallaClient
    
    item = frappe.get_doc("Item", item_name)
    
    if not item.salla_product_id or not item.salla_store:
        return
    
    try:
        client = SallaClient(item.salla_store)
        settings = get_settings()
        store = frappe.get_doc("Salla Store", item.salla_store)
        
        # Update price
        if settings.sync_price_to_salla:
            client.update_product(
                product_id=item.salla_product_id,
                data={"price": item.standard_rate}
            )
        
        # Update stock
        if settings.sync_stock_to_salla and store.warehouse:
            stock_qty = frappe.db.get_value(
                "Bin",
                {"item_code": item.item_code, "warehouse": store.warehouse},
                "actual_qty"
            ) or 0
            
            client.update_product_quantity(
                product_id=item.salla_product_id,
                quantity=int(stock_qty)
            )
        
    except Exception as e:
        frappe.log_error("Salla Item Push", f"Failed to push item to Salla: {str(e)}")


@frappe.whitelist()
def sync_product_from_salla(item_name, store_name):
    """
    Pull latest product data from Salla and update Item
    Called from item.js
    
    Args:
        item_name: Name of Item document
        store_name: Name of Salla Store
        
    Returns:
        Dict with success status
    """
    try:
        from salla_integration.utils.salla_client import SallaClient
        
        item = frappe.get_doc("Item", item_name)
        
        if not item.salla_product_id:
            return {
                "success": False,
                "error": "Item is not linked to any Salla product"
            }
        
        # Get product from Salla
        client = SallaClient(store_name)
        product_data = client.get_product(item.salla_product_id)
        
        if not product_data:
            return {
                "success": False,
                "error": "Product not found in Salla"
            }
        
        # Update item with Salla data
        update_item_from_salla(item_name, product_data, store_name)
        
        return {
            "success": True,
            "message": "Item synced successfully from Salla"
        }
        
    except Exception as e:
        frappe.log_error("Salla Product Sync", f"Failed to sync product from Salla: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


def on_item_delete(doc, method):
    """Called when Item is deleted"""
    # Log deletion if it was synced with Salla
    if doc.salla_product_id and doc.salla_store:
        frappe.log_error(
            "Salla Item Deletion",
            f"Deleted Item {doc.name} was synced with Salla Product ID: {doc.salla_product_id}"
        )


def on_stock_update(doc, method):
    """
    Called when Stock Ledger Entry is created
    Push stock updates to Salla if enabled
    """
    if not doc.item_code:
        return
    
    # Check if item is linked to Salla
    item = frappe.db.get_value(
        "Item",
        doc.item_code,
        ["salla_product_id", "salla_store"],
        as_dict=True
    )
    
    if not item or not item.salla_product_id or not item.salla_store:
        return
    
    settings = get_settings()
    if not settings.sync_stock_to_salla:
        return
    
    # Queue background job to update stock in Salla
    frappe.enqueue(
        method="salla_integration.api.items.push_item_to_salla",
        queue="short",
        item_name=doc.item_code,
        is_async=True
    )


@frappe.whitelist()
def sync_product_from_salla(item_name, store_name):
    """
    Pull latest product data from Salla and update Item
    Called from item.js
    
    Args:
        item_name: Name of Item document
        store_name: Name of Salla Store
        
    Returns:
        Dict with success status
    """
    try:
        from salla_integration.utils.salla_client import SallaClient
        
        item = frappe.get_doc("Item", item_name)
        
        if not item.salla_product_id:
            return {
                "success": False,
                "error": "Item is not linked to any Salla product"
            }
        
        # Get product from Salla
        client = SallaClient(store_name)
        product_data = client.get_product(item.salla_product_id)
        
        if not product_data:
            return {
                "success": False,
                "error": "Product not found in Salla"
            }
        
        # Update item with Salla data
        update_item_from_salla(item_name, product_data, store_name)
        
        return {
            "success": True,
            "message": "Item synced successfully from Salla"
        }
        
    except Exception as e:
        frappe.log_error("Salla Product Sync", f"Failed to sync product from Salla: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


def on_item_delete(doc, method):
    """Called when Item is deleted"""
    # Log deletion if it was synced with Salla
    if doc.salla_product_id and doc.salla_store:
        frappe.log_error(
            "Salla Item Deletion",
            f"Deleted Item {doc.name} was synced with Salla Product ID: {doc.salla_product_id}"
        )




def on_stock_update(doc, method):
    """
    Called when Stock Ledger Entry is created
    Push stock updates to Salla if enabled
    """
    if not doc.item_code:
        return
    
    # Check if item is linked to Salla
    item = frappe.db.get_value(
        "Item",
        doc.item_code,
        ["salla_product_id", "salla_store"],
        as_dict=True
    )
    
    if not item or not item.salla_product_id or not item.salla_store:
        return
    
    settings = get_settings()
    if not settings.sync_stock_to_salla:
        return
    
    # Queue background job to update stock in Salla
    frappe.enqueue(
        method="salla_integration.api.items.push_item_to_salla",
        queue="short",
        item_name=doc.item_code,
        is_async=True
    )


@frappe.whitelist()
def get_outdated_items():
    """
    Get items that haven't been synced in the last 24 hours
    Called from item_list.js
    
    Returns:
        List of item names
    """
    from frappe.utils import add_to_date
    
    yesterday = add_to_date(now(), hours=-24)
    
    items = frappe.get_all(
        "Item",
        filters={
            "salla_product_id": ["!=", ""],
            "salla_last_synced": ["<", yesterday]
        },
        pluck="name"
    )
    
    return items


@frappe.whitelist()
def bulk_push_items(item_names):
    """
    Push multiple items to Salla
    Called from item_list.js
    
    Args:
        item_names: List of item names (JSON string or list)
        
    Returns:
        Dict with success count
    """
    import json
    
    try:
        # Handle JSON string input
        if isinstance(item_names, str):
            item_names = json.loads(item_names)
        
        success_count = 0
        failed_count = 0
        
        for item_name in item_names:
            try:
                push_item_to_salla(item_name)
                success_count += 1
            except Exception as e:
                frappe.log_error(
                    "Bulk Item Push",
                    f"Failed to push item {item_name}: {str(e)}"
                )
                failed_count += 1
        
        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(item_names)
        }
        
    except Exception as e:
        frappe.log_error("Bulk Item Push", f"Bulk push failed: {str(e)}")
        return {
            "success": 0,
            "failed": len(item_names) if isinstance(item_names, list) else 0,
            "error": str(e)
        }


@frappe.whitelist()
def bulk_pull_items(item_names):
    """
    Pull multiple items from Salla
    Called from item_list.js
    
    Args:
        item_names: List of item names (JSON string or list)
        
    Returns:
        Dict with success count
    """
    import json
    
    try:
        # Handle JSON string input
        if isinstance(item_names, str):
            item_names = json.loads(item_names)
        
        success_count = 0
        failed_count = 0
        
        for item_name in item_names:
            try:
                item = frappe.get_doc("Item", item_name)
                
                if not item.salla_product_id or not item.salla_store:
                    failed_count += 1
                    continue
                
                result = sync_product_from_salla(item_name, item.salla_store)
                
                if result.get("success"):
                    success_count += 1
                else:
                    failed_count += 1
                    
            except Exception as e:
                frappe.log_error(
                    "Bulk Item Pull",
                    f"Failed to pull item {item_name}: {str(e)}"
                )
                failed_count += 1
        
        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(item_names)
        }
        
    except Exception as e:
        frappe.log_error("Bulk Item Pull", f"Bulk pull failed: {str(e)}")
        return {
            "success": 0,
            "failed": len(item_names) if isinstance(item_names, list) else 0,
            "error": str(e)
        }


@frappe.whitelist()
def bulk_unlink_items(item_names):
    """
    Unlink multiple items from Salla
    Called from item_list.js
    
    Args:
        item_names: List of item names (JSON string or list)
        
    Returns:
        Dict with success count
    """
    import json
    
    try:
        # Handle JSON string input
        if isinstance(item_names, str):
            item_names = json.loads(item_names)
        
        success_count = 0
        
        for item_name in item_names:
            try:
                frappe.db.set_value("Item", item_name, {
                    "salla_product_id": "",
                    "salla_store": "",
                    "salla_sku": "",
                    "salla_sync_hash": "",
                    "salla_last_synced": None
                })
                success_count += 1
            except Exception as e:
                frappe.log_error(
                    "Bulk Item Unlink",
                    f"Failed to unlink item {item_name}: {str(e)}"
                )
        
        frappe.db.commit()
        
        return {
            "success": success_count,
            "failed": len(item_names) - success_count,
            "total": len(item_names)
        }
        
    except Exception as e:
        frappe.log_error("Bulk Item Unlink", f"Bulk unlink failed: {str(e)}")
        return {
            "success": 0,
            "failed": len(item_names) if isinstance(item_names, list) else 0,
            "error": str(e)
        }


# Helpers for Salla group_products (product bundles)
def get_bundle_components(product_data):
    """
    Extract component products from a Salla group_products payload.
    Prefers the detailed `consisted_products` list; falls back to
    bundle.products when present.
    """
    if isinstance(product_data.get("consisted_products"), list) and product_data.get("consisted_products"):
        return product_data.get("consisted_products")
    
    bundle_products = []
    bundle_data = product_data.get("bundle")
    if isinstance(bundle_data, dict):
        bundle_products = bundle_data.get("products") or []
    
    normalized_components = []
    for product in bundle_products:
        if not isinstance(product, dict):
            continue
        normalized_components.append({
            "id": product.get("id"),
            "sku": product.get("sku"),
            "name": product.get("name"),
            "price": product.get("price"),
            "type": product.get("type") or "product",
            "quantity": product.get("qty") or product.get("quantity") or product.get("quantity_in_group"),
            "quantity_in_group": product.get("quantity_in_group") or product.get("qty") or 1
        })
    
    return normalized_components


def ensure_bundle_components(store_name, product_data):
    """
    Ensure all component products for a Salla bundle exist as ERPNext Items.
    
    Returns:
        List of dicts with item_code and qty for Product Bundle rows.
    """
    components = []
    
    for component in get_bundle_components(product_data):
        # Avoid nested bundles for now
        if component.get("type") == "group_products":
            frappe.log_error(
                "Salla Bundle Component Sync",
                f"Nested bundle detected for product {product_data.get('id')} -> {component.get('id')}"
            )
            continue
        
        try:
            sync_products(store_name, component)
        except Exception as comp_err:
            frappe.log_error(
                "Salla Bundle Component Sync",
                f"Failed to sync component {component.get('id')}: {str(comp_err)}"
            )
            continue
        
        component_item_code = frappe.db.get_value(
            "Item",
            {"salla_product_id": str(component.get("id")), "salla_store": store_name},
            "name"
        )
        
        if not component_item_code and component.get("sku"):
            component_item_code = frappe.db.get_value(
                "Item",
                {"item_code": component.get("sku")},
                "name"
            )
        
        if not component_item_code:
            frappe.log_error(
                "Salla Bundle Component Sync",
                f"Component item missing for Salla product {component.get('id')} (sku: {component.get('sku')})"
            )
            continue
        
        qty = component.get("quantity_in_group") or component.get("qty") or 1
        try:
            qty = float(qty or 1)
        except Exception:
            qty = 1
        
        components.append({
            "item_code": component_item_code,
            "qty": qty
        })
    
    return components


def create_or_update_product_bundle(parent_item_code, components):
    """
    Create or refresh an ERPNext Product Bundle for a Salla group product.
    """
    if not parent_item_code or not components:
        return
    
    existing_bundle = frappe.db.exists("Product Bundle", {"new_item_code": parent_item_code})
    if existing_bundle:
        bundle_doc = frappe.get_doc("Product Bundle", existing_bundle)
        bundle_doc.items = []
    else:
        bundle_doc = frappe.get_doc({
            "doctype": "Product Bundle",
            "new_item_code": parent_item_code,
            "items": []
        })
    
    for component in components:
        item_code = component.get("item_code")
        if not item_code:
            continue
        
        qty = component.get("qty") or 1
        bundle_doc.append("items", {
            "item_code": item_code,
            "qty": qty
        })
    
    bundle_doc.save(ignore_permissions=True)
    frappe.db.commit()
    return bundle_doc.name


def sync_group_product(store_name, product_data):
    """
    Handle Salla group_products by creating a non-stock item and Product Bundle.
    """
    components = ensure_bundle_components(store_name, product_data)
    salla_product_id = str(product_data.get("id"))
    
    existing_item = frappe.db.get_value(
        "Item",
        {"salla_product_id": salla_product_id, "salla_store": store_name},
        "name"
    )
    
    if existing_item:
        parent_item = update_item_from_salla(
            existing_item,
            product_data,
            store_name,
            is_bundle=True
        ) or existing_item
    else:
        parent_item = create_item_from_salla(
            product_data,
            store_name,
            is_bundle=True
        )
    
    if components:
        create_or_update_product_bundle(parent_item, components)
    
    return parent_item