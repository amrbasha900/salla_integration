# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from salla_integration.utils.salla_client import SallaClient

@frappe.whitelist()
def test_connection(store_name):
    """
    Test connection to Salla API
    
    Args:
        store_name: Name of Salla Store
        
    Returns:
        Connection test result
    """
    try:
        client = SallaClient(store_name)
        result = client.test_connection()
        
        return result
        
    except Exception as e:
        frappe.log_error(f"Connection test failed: {str(e)}", "Salla Connection Test")
        return {
            "success": False,
            "error": str(e)
        }