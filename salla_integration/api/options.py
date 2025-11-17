import frappe
import json
from typing import Dict, Any, List
from salla_integration.utils.salla_client import SallaClient
from salla_integration.salla_integration.doctype.salla_sync_log.salla_sync_log import create_sync_log
from salla_integration.salla_integration.doctype.missing_products_sku.missing_products_sku import log_missing_sku

def _text(value) -> str:
    """Coerce any value to trimmed string safely."""
    if value is None:
        return ""
    try:
        # Handle different data types properly
        if isinstance(value, (int, float)):
            return str(value).strip()
        elif isinstance(value, str):
            return value.strip()
        else:
            return str(value).strip()
    except Exception:
        return ""


def _safe_get_dict_value(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Safely get value from dictionary with proper None handling."""
    if not isinstance(data, dict):
        return default
    return data.get(key, default)


def _ensure_option_doctype(store_name: str, product_id: str, product_sku: str, option: Dict[str, Any]) -> str:
    """
    Create or update Salla Product Option.
    - De-duplicate by (salla_store, option_name) regardless of product.
    - Merge values: if same value name appears, append unique SKUs and update fields.
    """
    if not isinstance(option, dict):
        frappe.log_error("Salla Option Sync", f"Invalid option data type: {type(option)}")
        return ""
    
    option_name = _text(_safe_get_dict_value(option, "name"))
    if not option_name:
        frappe.log_error("Salla Option Sync", f"Missing option name in option: {option}")
        return ""
    
    try:
        raw_json = json.dumps(option, ensure_ascii=False)
    except Exception as e:
        frappe.log_error("Salla Option Sync", f"Failed to serialize option to JSON: {str(e)}")
        raw_json = "{}"
    
    # Find existing option by (store + option_name + product_sku) OR migrate legacy records
    name = None
    try:
        name = frappe.db.get_value(
            "Salla Product Option",
            {"salla_store": store_name, "option_name": option_name, "product_sku": product_sku},
            "name"
        )
    except Exception as e:
        frappe.log_error("Salla Option Sync", f"Failed to query existing option: {str(e)}")

    # If not found, try by store + option_id (legacy without product_sku) and upgrade it
    if not name:
        try:
            legacy_by_id = frappe.db.get_value(
                "Salla Product Option",
                {"salla_store": store_name, "option_id": str(_safe_get_dict_value(option, "id") or "")},
                "name"
            )
            if legacy_by_id:
                try:
                    legacy_doc = frappe.get_doc("Salla Product Option", legacy_by_id)
                    legacy_doc.product_sku = product_sku
                    legacy_doc.product_id = product_id
                    if not _text(getattr(legacy_doc, "option_name", None)):
                        legacy_doc.option_name = option_name
                    legacy_doc.save(ignore_permissions=True)
                    name = legacy_doc.name
                except Exception as e:
                    frappe.log_error("Salla Option Sync", f"Failed to upgrade legacy option by id: {str(e)}")
        except Exception:
            pass

    # If still not found, try legacy by store + option_name with empty product_sku and upgrade it
    if not name:
        try:
            legacy_by_name = frappe.db.get_value(
                "Salla Product Option",
                {"salla_store": store_name, "option_name": option_name, "product_sku": ["in", ["", None]]},
                "name"
            )
            if legacy_by_name:
                try:
                    legacy_doc2 = frappe.get_doc("Salla Product Option", legacy_by_name)
                    legacy_doc2.product_sku = product_sku
                    legacy_doc2.product_id = product_id
                    legacy_doc2.save(ignore_permissions=True)
                    name = legacy_doc2.name
                except Exception as e:
                    frappe.log_error("Salla Option Sync", f"Failed to upgrade legacy option by name: {str(e)}")
        except Exception:
            pass

    try:
        if name:
            doc = frappe.get_doc("Salla Product Option", name)
        else:
            doc = frappe.get_doc({
                "doctype": "Salla Product Option",
                "salla_store": store_name,
                "product_id": product_id,
                "option_id": str(_safe_get_dict_value(option, "id") or _safe_get_dict_value(option, "option_id") or ""),
                "option_name": option_name,
                "product_sku": product_sku
            })

        # Update parent fields with safe getters
        doc.option_name = option_name
        doc.option_type = _text(_safe_get_dict_value(option, "type"))
        doc.required = int(bool(_safe_get_dict_value(option, "required")))
        doc.display_type = _text(_safe_get_dict_value(option, "display_type"))
        doc.visibility = _text(_safe_get_dict_value(option, "visibility"))
        doc.raw_json = raw_json
        doc.product_sku = product_sku
        doc.product_id = product_id

        # Build existing child map by value name - safe handling
        existing_map = {}
        for row in (doc.get("values") or []):
            try:
                display_val = getattr(row, "display_value", None)
                value_name = getattr(row, "value_name", None)
                key = _text(display_val) or _text(value_name)
                if key:
                    existing_map[key] = row
            except Exception as e:
                frappe.log_error("Salla Option Sync", f"Error processing existing value row: {str(e)}")
                continue

        # Merge incoming values
        option_values = _safe_get_dict_value(option, "values", []) or []
        if not isinstance(option_values, list):
            option_values = []
            
        for val in option_values:
            if not isinstance(val, dict):
                continue
                
            display_value = _text(_safe_get_dict_value(val, "display_value"))
            name_value = _text(_safe_get_dict_value(val, "name"))
            value_label = display_value or name_value
            
            if not value_label:
                continue
                
            key = value_label
            val_id = str(_safe_get_dict_value(val, "id") or "")
            val_price = _safe_get_dict_value(val, "price", {})
            
            # Handle price safely
            price_amount = 0
            currency = ""
            if isinstance(val_price, dict):
                price_amount = _safe_get_dict_value(val_price, "amount", 0) or 0
                currency = _text(_safe_get_dict_value(val_price, "currency"))
            else:
                price_amount = _safe_get_dict_value(val, "price", 0) or 0

            if key in existing_map:
                row = existing_map[key]
                # Update basic fields safely
                row.value_id = row.value_id or val_id
                row.value_name = row.value_name or name_value
                row.display_value = row.display_value or display_value
                
                # Update price/currency/image if empty
                if not row.price_amount:
                    row.price_amount = price_amount
                if not row.currency:
                    row.currency = currency
                if not row.image_url:
                    row.image_url = _text(_safe_get_dict_value(val, "image_url"))
            else:
                # Append as new value
                doc.append("values", {
                    "value_id": val_id,
                    "value_name": name_value,
                    "display_value": display_value,
                    "price_amount": price_amount,
                    "currency": currency,
                    "image_url": _text(_safe_get_dict_value(val, "image_url"))
                })

        # Save/insert
        if doc.get("name"):
            doc.save(ignore_permissions=True)
        else:
            doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name
        
    except Exception as e:
        frappe.log_error("Salla Option Sync", f"Failed to save option document: {str(e)}")
        return ""


def _ensure_item_attribute_for_option(store_name: str, product_sku: str, option: Dict[str, Any]):
    """
    Create/Update ERPNext Item Attribute and Attribute Values from Salla option/value.
    Add Salla IDs as custom fields to support mapping.
    """
    if not isinstance(option, dict):
        frappe.log_error("Salla Option Sync", f"Invalid option data for attribute creation: {type(option)}")
        return
    
    option_name = _text(_safe_get_dict_value(option, "name"))
    option_id = str(_safe_get_dict_value(option, "id") or "")
    
    if not option_name:
        option_name = f"Salla Option {option_id}" if option_id else "Unknown Option"

    try:
        # Create or fetch Item Attribute (dedupe legacy names)
        desired_attr_name = f"{product_sku} - {option_id} - {option_name}".strip()
        # 1) Exact composed name
        attr_name = frappe.db.get_value("Item Attribute", {"attribute_name": desired_attr_name}, "name")
        if not attr_name:
            # 2) Existing attribute by salla_option_id + store (older runs)
            attr_name = frappe.db.get_value(
                "Item Attribute",
                {"salla_option_id": option_id, "salla_store": store_name},
                "name"
            )
        if not attr_name:
            # 3) Legacy attribute using plain option_name (created by older code)
            legacy_name = frappe.db.get_value("Item Attribute", {"attribute_name": option_name}, "name")
            if legacy_name:
                try:
                    legacy_attr = frappe.get_doc("Item Attribute", legacy_name)
                    # Rename attribute_name to the composed name to avoid duplicates
                    legacy_attr.attribute_name = desired_attr_name
                    legacy_attr.save(ignore_permissions=True)
                    attr_name = legacy_attr.name
                except Exception as e:
                    frappe.log_error("Salla Option Sync", f"Failed to migrate legacy attribute name: {str(e)}")
        if not attr_name:
            # 4) Create fresh attribute with composed name
            try:
                attr = frappe.get_doc({
                    "doctype": "Item Attribute",
                    "attribute_name": desired_attr_name,
                    "from_range": 0,
                    "numeric_values": 0
                })
                attr.insert(ignore_permissions=True)
                attr_name = attr.name
            except Exception as e:
                frappe.log_error("Salla Option Sync", f"Failed to create Item Attribute: {str(e)}")
                return

        # Ensure custom fields exist (idempotent)
        _add_attribute_custom_fields()

        # Set Salla Option ID and Store on Item Attribute
        frappe.db.set_value("Item Attribute", attr_name, {
            "salla_option_id": option_id,
            "salla_store": store_name,
            "product_sku": product_sku
        })

        # Ensure values exist (append-only; no direct child inserts or raw SQL)
        attr_doc = frappe.get_doc("Item Attribute", attr_name)
        existing_values = {v.attribute_value: v for v in (attr_doc.item_attribute_values or [])}
        
        option_values = _safe_get_dict_value(option, "values", []) or []
        if not isinstance(option_values, list):
            option_values = []
            
        for val in option_values:
            if not isinstance(val, dict):
                continue
                
            name_value = _text(_safe_get_dict_value(val, "name"))
            display_value = _text(_safe_get_dict_value(val, "display_value"))
            hashed_value = _text(_safe_get_dict_value(val, "hashed_display_value"))
            fallback_id = str(_safe_get_dict_value(val, "id") or "")
            # Prefer 'name' from response over display fields
            value_name = name_value or display_value or hashed_value or fallback_id
            val_id = str(_safe_get_dict_value(val, "id") or "")
            
            if not value_name:
                continue
                
            if value_name not in existing_values:
                try:
                    attr_doc.append("item_attribute_values", {
                        "attribute_value": value_name,
                        "abbr": value_name
                    })
                    existing_values[value_name] = True
                except Exception as e:
                    frappe.log_error("Salla Option Sync", f"Failed to append attribute value on parent: {str(e)}")
        
        try:
            attr_doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error("Salla Option Sync", f"Saving Item Attribute failed: {str(e)}")

        # Update child values with Salla IDs by editing child rows and saving
        for val in option_values:
            if not isinstance(val, dict):
                continue
                
            name_value = _text(_safe_get_dict_value(val, "name"))
            display_value = _text(_safe_get_dict_value(val, "display_value"))
            hashed_value = _text(_safe_get_dict_value(val, "hashed_display_value"))
            fallback_id = str(_safe_get_dict_value(val, "id") or "")
            # Prefer 'name' from response over display fields
            value_name = name_value or display_value or hashed_value or fallback_id
            val_id = str(_safe_get_dict_value(val, "id") or "")
            
            if not value_name or not val_id:
                continue
                
            # Find in-memory child and set mapping
            for row in (attr_doc.item_attribute_values or []):
                if _text(getattr(row, "attribute_value", None)) == value_name:
                    try:
                        row.salla_option_value_id = val_id
                        if not _text(getattr(row, "abbr", None)):
                            row.abbr = value_name
                    except Exception as e:
                        frappe.log_error("Salla Option Sync", f"Failed to set child mapping: {str(e)}")
                    break
        try:
            attr_doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error("Salla Option Sync", f"Failed to save Item Attribute after child update: {str(e)}")
        
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error("Salla Option Sync", f"Failed to ensure item attribute: {str(e)}")


def _add_attribute_custom_fields():
    """Create custom fields for Item Attribute and Item Attribute Value to store Salla IDs."""
    try:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields({
            "Item Attribute": [
                {
                    "fieldname": "salla_option_id",
                    "label": "Salla Option ID",
                    "fieldtype": "Data",
                    "insert_after": "numeric_values",
                    "read_only": 1
                },
                {
                    "fieldname": "salla_store",
                    "label": "Salla Store",
                    "fieldtype": "Link",
                    "options": "Salla Store",
                    "insert_after": "salla_option_id"
				},
				{
					"fieldname": "product_sku",
					"label": "Product SKU",
					"fieldtype": "Data",
					"insert_after": "salla_store",
					"read_only": 1
                }
            ],
            "Item Attribute Value": [
                {
                    "fieldname": "salla_option_value_id",
                    "label": "Salla Option Value ID",
                    "fieldtype": "Data",
                    "insert_after": "abbr",
                    "read_only": 1
                }
            ]
        }, update=True)
    except Exception as e:
        frappe.log_error("Salla Option Sync", f"Failed to add custom fields: {str(e)}")


def sync_product_options(store_name: str, product_or_data: Any, sync_log_name: str = None):
    """
    Sync Salla product options to:
    - Salla Product Option (parent) with Salla Product Option Value (child)
    - ERPNext Item Attribute and Item Attribute Values (with custom fields)
    - Log missing SKU values as skipped in Salla Sync Log
    """
    log = None
    try:
        if sync_log_name:
            log = frappe.get_doc("Salla Sync Log", sync_log_name)
        else:
            log = create_sync_log(store_name, "Products")
    except Exception as e:
        frappe.log_error("Salla Option Sync", f"Failed to get sync log: {str(e)}")
        return {"total": 0, "skipped": 0, "errors": [f"Failed to get sync log: {str(e)}"]}

    try:
        client = SallaClient(store_name)
        
        # Accept product dict or product id
        product = {}
        product_id = ""
        
        if isinstance(product_or_data, dict):
            product_id = str(_safe_get_dict_value(product_or_data, "id", ""))
            product = product_or_data
        else:
            product_id = str(product_or_data) if product_or_data is not None else ""
            try:
                product = client.get_product(product_id) or {}
            except Exception as e:
                frappe.log_error("Salla Option Sync", f"Failed to fetch product {product_id}: {str(e)}")
                product = {}

        options = _safe_get_dict_value(product, "options", []) or []
        
        # Fallback: fetch full product details if options not present
        if not options and product_id:
            try:
                full = client.get_product(product_id) or {}
                options = _safe_get_dict_value(full, "options", []) or []
                product = full or product
            except Exception as e:
                frappe.log_error("Salla Option Sync", f"Failed to fetch full product details for {product_id}: {str(e)}")
                options = []

        if not isinstance(options, list):
            options = []

        total = 0
        skipped = 0
        errors: List[str] = []
        
        for opt in options:
            total += 1
            try:
                if not isinstance(opt, dict):
                    errors.append(f"Invalid option data type for product {product_id}: {type(opt)}")
                    log.increment_failed()
                    continue
                
                product_sku = _text(_safe_get_dict_value(product, "sku"))
                if not product_sku:
                    # Skip when product has no SKU as requested
                    try:
                        log_missing_sku(store_name, product_id, _text(_safe_get_dict_value(product, "name")), "Product", "Options skipped: product missing SKU")
                    except Exception:
                        pass
                    continue
                docname = _ensure_option_doctype(store_name, product_id, product_sku, opt)
                if docname:
                    _ensure_item_attribute_for_option(store_name, product_sku, opt)
                            
            except Exception as e:
                # Add context to quickly identify the offending fields
                try:
                    opt_name = _text(_safe_get_dict_value(opt, "name")) if isinstance(opt, dict) else str(type(opt))
                except Exception:
                    opt_name = "<unreadable>"
                
                msg = f"Failed option sync for product {product_id}: {str(e)} | option_name={opt_name!r}"
                errors.append(msg)
                frappe.log_error("Salla Option Sync", msg)
                if log:
                    log.increment_failed()

        # Update log totals
        if log:
            log.total_records = (log.total_records or 0) + total
            if errors:
                # append errors once to avoid version conflicts
                existing = log.error_message or ""
                combined = (existing + ("\n" if existing else "") + "\n".join(errors)).strip()
                frappe.db.set_value("Salla Sync Log", log.name, "error_message", combined)
                frappe.db.commit()
        
        frappe.db.commit()
        return {"total": total, "skipped": skipped, "errors": errors}
        
    except Exception as e:
        error_msg = f"Critical error in sync_product_options: {str(e)}"
        frappe.log_error("Salla Option Sync Critical", error_msg)
        return {"total": 0, "skipped": 0, "errors": [error_msg]}