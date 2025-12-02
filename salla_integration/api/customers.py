# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe import _
from frappe.utils import now
from salla_integration.api.customer_groups import resolve_customer_groups
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
    get_settings, should_auto_create_customers
)

def sync_customer(store_name, customer_data):
    """
    Sync a single customer from Salla to ERPNext Customer
    
    Args:
        store_name: Name of Salla Store
        customer_data: Customer data from Salla API
    """
    if not should_auto_create_customers():
        return
    
    salla_customer_id = str(customer_data.get('id'))
    
    # Check if customer already exists
    existing_customer = frappe.db.get_value(
        "Customer",
        {"salla_customer_id": salla_customer_id, "salla_store": store_name},
        "name"
    )
    
    if existing_customer:
        # Update existing customer
        update_customer_from_salla(existing_customer, customer_data, store_name)
        return existing_customer
    else:
        # Create new customer
        return create_customer_from_salla(customer_data, store_name)


def create_customer_from_salla(customer_data, store_name):
    """
    Create new Customer from Salla customer data
    
    Args:
        customer_data: Customer data from Salla API
        store_name: Name of Salla Store
        
    Returns:
        Customer name
    """
    store = frappe.get_doc("Salla Store", store_name)
    settings = get_settings()
    
    # Prepare customer name
    first_name = customer_data.get('first_name', '')
    last_name = customer_data.get('last_name', '')
    full_name = f"{first_name} {last_name}".strip()
    
    if not full_name:
        full_name = customer_data.get('email', f"Customer-{customer_data.get('id')}")
    
    # Get customer group and territory
    customer_group = store.default_customer_group or settings.default_customer_group or "Individual"
    territory = store.default_territory or settings.default_territory or "All Territories"
    
    # Create customer
    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": full_name,
        "customer_type": "Individual",
        "customer_group": customer_group,
        "territory": territory,
        # Salla fields
        "salla_is_from_salla": 1,
        "salla_store": store_name,
        "salla_customer_id": str(customer_data.get('id')),
        "salla_last_synced": now()
    })
    
    # Add email if available
    if customer_data.get('email'):
        customer.email_id = customer_data.get('email')
    
    # Add mobile if available
    if customer_data.get('mobile') or customer_data.get('phone'):
        customer.mobile_no = customer_data.get('mobile') or customer_data.get('phone')
    
    update_customer_group_links(customer, store_name, customer_data)

    try:
        customer.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Create address if available
        if customer_data.get('address'):
            create_customer_address(customer.name, customer_data.get('address'), store_name)
        
        # Create contact if email/mobile available
        if customer_data.get('email') or customer_data.get('mobile'):
            create_customer_contact(customer.name, customer_data)
        
        frappe.logger().info(f"Created customer: {customer.name} from Salla customer: {customer_data.get('id')}")
        
        return customer.name
        
    except Exception as e:
        frappe.log_error(f"Failed to create customer: {str(e)}", "Salla Customer Creation")
        raise


def update_customer_from_salla(customer_name, customer_data, store_name):
    """
    Update existing Customer with Salla customer data
    
    Args:
        customer_name: ERPNext Customer name
        customer_data: Customer data from Salla API
        store_name: Name of Salla Store
    """
    customer = frappe.get_doc("Customer", customer_name)
    
    # Update customer fields
    first_name = customer_data.get('first_name', '')
    last_name = customer_data.get('last_name', '')
    full_name = f"{first_name} {last_name}".strip()
    
    if full_name:
        customer.customer_name = full_name
    
    if customer_data.get('email'):
        customer.email_id = customer_data.get('email')
    
    if customer_data.get('mobile') or customer_data.get('phone'):
        customer.mobile_no = customer_data.get('mobile') or customer_data.get('phone')
    
    customer.salla_last_synced = now()
    update_customer_group_links(customer, store_name, customer_data)
    
    try:
        customer.save(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(f"Updated customer: {customer.name} from Salla customer: {customer_data.get('id')}")
        
    except Exception as e:
        frappe.log_error(f"Failed to update customer: {str(e)}", "Salla Customer Update")
        raise


def get_or_create_customer(customer_data, store_name):
    """
    Get existing customer or create new one
    
    Args:
        customer_data: Customer data from Salla
        store_name: Name of Salla Store
        
    Returns:
        Customer name
    """
    if not customer_data or not customer_data.get('id'):
        # Return default customer if no customer data
        return get_default_customer(store_name)
    
    salla_customer_id = str(customer_data.get('id'))
    
    # Check if customer exists
    existing_customer = frappe.db.get_value(
        "Customer",
        {"salla_customer_id": salla_customer_id, "salla_store": store_name},
        "name"
    )
    
    if existing_customer:
        return existing_customer
    
    # Create new customer
    return sync_customer(store_name, customer_data)


def get_default_customer(store_name):
    """
    Get or create default customer for guest orders
    
    Args:
        store_name: Name of Salla Store
        
    Returns:
        Customer name
    """
    store = frappe.get_doc("Salla Store", store_name)
    
    customer_name = f"Guest Customer - {store_name}"
    
    if frappe.db.exists("Customer", customer_name):
        return customer_name
    
    # Create default guest customer
    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_type": "Individual",
        "customer_group": store.default_customer_group or "Individual",
        "territory": store.default_territory or "All Territories",
        "salla_store": store_name,
        "salla_is_from_salla": 1
    })
    
    customer.insert(ignore_permissions=True)
    frappe.db.commit()
    
    return customer.name


def create_customer_address(customer_name, address_data, store_name):
    """
    Create address for customer
    
    Args:
        customer_name: ERPNext Customer name
        address_data: Address data from Salla
        store_name: Name of Salla Store
    """
    if not address_data:
        return
    
    # Check if address already exists
    existing_address = frappe.db.exists("Address", {
        "address_line1": address_data.get('street', ''),
        "city": address_data.get('city', ''),
        "country": address_data.get('country', '')
    })
    
    if existing_address:
        # Link existing address to customer
        link_address_to_customer(existing_address, customer_name)
        return
    
    # Create new address
    address = frappe.get_doc({
        "doctype": "Address",
        "address_title": customer_name,
        "address_type": "Billing",
        "address_line1": address_data.get('street', ''),
        "address_line2": address_data.get('block', ''),
        "city": address_data.get('city', ''),
        "state": address_data.get('state', ''),
        "country": address_data.get('country', ''),
        "pincode": address_data.get('postal_code', ''),
        "salla_is_from_salla": 1,
        "links": [{
            "link_doctype": "Customer",
            "link_name": customer_name
        }]
    })
    
    try:
        address.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Failed to create address: {str(e)}", "Salla Address Creation")


def link_address_to_customer(address_name, customer_name):
    """Link existing address to customer"""
    address = frappe.get_doc("Address", address_name)
    
    # Check if already linked
    for link in address.links:
        if link.link_doctype == "Customer" and link.link_name == customer_name:
            return
    
    # Add link
    address.append("links", {
        "link_doctype": "Customer",
        "link_name": customer_name
    })
    
    address.save(ignore_permissions=True)
    frappe.db.commit()


def create_customer_contact(customer_name, customer_data):
    """
    Create contact for customer
    
    Args:
        customer_name: ERPNext Customer name
        customer_data: Customer data from Salla
    """
    email = customer_data.get('email')
    mobile = customer_data.get('mobile') or customer_data.get('phone')
    
    if not email and not mobile:
        return
    
    # Check if contact already exists
    if email:
        existing_contact = frappe.db.exists("Contact", {
            "email_id": email
        })
        
        if existing_contact:
            # Link existing contact to customer
            link_contact_to_customer(existing_contact, customer_name)
            return
    
    first_name = customer_data.get('first_name', '')
    last_name = customer_data.get('last_name', '')
    
    # Create new contact
    contact = frappe.get_doc({
        "doctype": "Contact",
        "first_name": first_name or "Customer",
        "last_name": last_name or "",
        "salla_is_from_salla": 1,
        "links": [{
            "link_doctype": "Customer",
            "link_name": customer_name
        }]
    })
    
    # Add email
    if email:
        contact.append("email_ids", {
            "email_id": email,
            "is_primary": 1
        })
    
    # Add mobile
    if mobile:
        contact.append("phone_nos", {
            "phone": mobile,
            "is_primary_mobile_no": 1
        })
    
    try:
        contact.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Failed to create contact: {str(e)}", "Salla Contact Creation")


def link_contact_to_customer(contact_name, customer_name):
    """Link existing contact to customer"""
    contact = frappe.get_doc("Contact", contact_name)
    
    # Check if already linked
    for link in contact.links:
        if link.link_doctype == "Customer" and link.link_name == customer_name:
            return
    
    # Add link
    contact.append("links", {
        "link_doctype": "Customer",
        "link_name": customer_name
    })
    
    contact.save(ignore_permissions=True)
    frappe.db.commit()


def update_customer_group_links(customer_doc, store_name, customer_data):
    """
    Update the Salla Customer Group child table on a Customer doc.
    """
    if not customer_doc or not customer_doc.meta.get_field("salla_customer_groups"):
        return

    groups = _extract_salla_groups(customer_data)
    customer_doc.set("salla_customer_groups", [])

    if not groups:
        return

    resolved_groups = resolve_customer_groups(store_name, groups)
    if not resolved_groups:
        return

    for group_name in resolved_groups:
        customer_doc.append("salla_customer_groups", {"customer_group": group_name})

    # Use the first resolved group as the primary customer group if available
    if resolved_groups[0]:
        customer_doc.customer_group = resolved_groups[0]


def _extract_salla_groups(customer_data):
    """
    Normalize the groups payload returned by Salla customers endpoint.
    """
    if not customer_data:
        return []

    groups = customer_data.get("groups") or customer_data.get("customer_groups")
    if not groups:
        return []

    # API might return dicts with nested data arrays
    if isinstance(groups, dict):
        if groups.get("data") and isinstance(groups.get("data"), list):
            groups = groups.get("data")
        else:
            groups = [groups]

    if not isinstance(groups, list):
        groups = [groups]

    normalized = []
    for entry in groups:
        if not entry:
            continue
        if isinstance(entry, dict) and entry.get("data") and isinstance(entry["data"], list):
            normalized.extend(entry["data"])
        else:
            normalized.append(entry)

    return normalized