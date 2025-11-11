# Copyright (c) 2025, Amr Basha and contributors
# For license information, please see license.txt


import frappe
from frappe.model.document import Document
from frappe.utils import now, time_diff_in_seconds
import json

class SallaSyncLog(Document):
    def before_save(self):
        """Calculate duration if completed"""
        if self.status in ["Completed", "Failed", "Partially Completed"] and not self.completed_at:
            self.completed_at = now()
    
    def get_duration(self):
        """Get sync duration in seconds"""
        if self.started_at and self.completed_at:
            return time_diff_in_seconds(self.completed_at, self.started_at)
        return 0
    
    def log_success(self):
        """Mark sync as successful (atomic, avoid version conflicts)"""
        frappe.db.set_value("Salla Sync Log", self.name, {
            "status": "Completed",
            "completed_at": now()
        })
        frappe.db.commit()
        self.reload()
    
    def log_failure(self, error_message, traceback=None):
        """Mark sync as failed with error details (atomic)"""
        values = {
            "status": "Failed",
            "error_message": error_message,
            "completed_at": now()
        }
        if traceback:
            values["traceback"] = traceback
        frappe.db.set_value("Salla Sync Log", self.name, values)
        frappe.db.commit()
        self.reload()
    
    def log_partial(self, error_message=None):
        """Mark sync as partially completed (atomic)"""
        values = {
            "status": "Partially Completed",
            "completed_at": now()
        }
        if error_message:
            # Append to existing error_message
            existing = self.error_message or ""
            values["error_message"] = (existing + ("\n" if existing else "") + error_message).strip()
        frappe.db.set_value("Salla Sync Log", self.name, values)
        frappe.db.commit()
        self.reload()
    
    def increment_success(self):
        """Increment successful record count atomically to avoid version conflicts"""
        frappe.db.sql(
            "update `tabSalla Sync Log` set successful_records = coalesce(successful_records,0) + 1 where name=%s",
            (self.name,)
        )
        self.reload()
    
    def increment_failed(self):
        """Increment failed record count atomically to avoid version conflicts"""
        frappe.db.sql(
            "update `tabSalla Sync Log` set failed_records = coalesce(failed_records,0) + 1 where name=%s",
            (self.name,)
        )
        self.reload()
    
    def increment_skipped(self):
        """Increment skipped record count atomically to avoid version conflicts"""
        frappe.db.sql(
            "update `tabSalla Sync Log` set skipped_records = coalesce(skipped_records,0) + 1 where name=%s",
            (self.name,)
        )
        self.reload()
    
    def add_sync_data(self, key, value):
        """Add data to sync_data JSON field"""
        if not self.sync_data:
            self.sync_data = "{}"
        
        data = json.loads(self.sync_data)
        data[key] = value
        self.sync_data = json.dumps(data, indent=2)
        self.save(ignore_permissions=True)


def create_sync_log(salla_store, sync_type, total_records=0):
    """
    Helper function to create a new sync log
    
    Args:
        salla_store: Name of Salla Store
        sync_type: Type of sync (Products, Orders, etc.)
        total_records: Total number of records to process
        
    Returns:
        SallaSyncLog document
    """
    log = frappe.get_doc({
        "doctype": "Salla Sync Log",
        "salla_store": salla_store,
        "sync_type": sync_type,
        "status": "In Progress",
        "started_at": now(),
        "total_records": total_records,
        "successful_records": 0,
        "failed_records": 0,
        "skipped_records": 0
    })
    
    log.insert(ignore_permissions=True)
    frappe.db.commit()
    
    return log


def get_recent_logs(salla_store=None, sync_type=None, limit=10):
    """
    Get recent sync logs with filters
    
    Args:
        salla_store: Filter by store (optional)
        sync_type: Filter by sync type (optional)
        limit: Number of records to return
        
    Returns:
        List of sync logs
    """
    filters = {}
    
    if salla_store:
        filters["salla_store"] = salla_store
    
    if sync_type:
        filters["sync_type"] = sync_type
    
    return frappe.get_all(
        "Salla Sync Log",
        filters=filters,
        fields=["name", "salla_store", "sync_type", "status", "started_at", 
                "completed_at", "total_records", "successful_records", 
                "failed_records"],
        order_by="started_at desc",
        limit=limit
    )