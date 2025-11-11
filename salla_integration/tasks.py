# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe.utils import now

def refresh_store_tokens():
    """
    Scheduled task to refresh access tokens for all stores
    Runs daily at 2 AM via cron
    """
    stores = frappe.get_all(
        "Salla Store",
        filters={"is_authorized": 1},
        pluck="name"
    )
    
    for store_name in stores:
        try:
            store = frappe.get_doc("Salla Store", store_name)
            
            # Check if token will expire in next 24 hours
            if store.is_token_expired():
                frappe.logger().info(f"Refreshing token for store: {store_name}")
                store.refresh_access_token()
                
        except Exception as e:
            frappe.log_error(
                f"Failed to refresh token for {store_name}: {str(e)}",
                "Salla Token Refresh Task"
            )


def sync_all_stores_daily():
    """
    Scheduled task to sync all active stores
    Runs daily
    """
    from salla_integration.api.sync import sync_store_products, sync_store_orders, sync_store_categories
    
    stores = frappe.get_all(
        "Salla Store",
        filters={
            "is_authorized": 1,
            "enable_product_sync": 1
        },
        pluck="name"
    )
    
    for store_name in stores:
        try:
            frappe.logger().info(f"Starting daily sync for store: {store_name}")
            
            # Sync categories
            sync_store_categories(store_name)
            
            # Sync products
            sync_store_products(store_name)
            
            # Sync orders
            sync_store_orders(store_name)
            
            # Update last sync time
            frappe.db.set_value(
                "Salla Store",
                store_name,
                "last_sync_datetime",
                now()
            )
            
            frappe.db.commit()
            
        except Exception as e:
            frappe.log_error(
                f"Daily sync failed for {store_name}: {str(e)}",
                "Salla Daily Sync Task"
            )


def sync_inventory_to_salla():
    """
    Scheduled task to sync inventory changes to Salla
    Can be configured to run every 15 minutes
    """
    from salla_integration.api.sync import push_inventory_updates
    
    stores = frappe.get_all(
        "Salla Store",
        filters={
            "is_authorized": 1,
            "enable_inventory_sync": 1
        },
        pluck="name"
    )
    
    for store_name in stores:
        try:
            push_inventory_updates(store_name)
            
        except Exception as e:
            frappe.log_error(
                f"Inventory sync failed for {store_name}: {str(e)}",
                "Salla Inventory Sync Task"
            )


def cleanup_old_sync_logs():
    """
    Delete sync logs older than 30 days
    Runs daily
    """
    from frappe.utils import add_days
    
    cutoff_date = add_days(frappe.utils.today(), -30)
    
    try:
        # Delete old logs
        old_logs = frappe.get_all(
            "Salla Sync Log",
            filters={
                "started_at": ["<", cutoff_date]
            },
            pluck="name"
        )
        
        for log_name in old_logs:
            frappe.delete_doc("Salla Sync Log", log_name, ignore_permissions=True)
        
        if old_logs:
            frappe.db.commit()
            frappe.logger().info(f"Cleaned up {len(old_logs)} old Salla sync logs")
            
    except Exception as e:
        frappe.log_error(f"Failed to cleanup sync logs: {str(e)}", "Salla Cleanup Task")


def check_failed_syncs():
    """
    Check for failed syncs and send notifications
    Runs hourly
    """
    from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import get_settings
    
    settings = get_settings()
    
    if not settings.notify_on_sync_failure or not settings.notification_email:
        return
    
    try:
        # Get failed syncs from last hour
        one_hour_ago = frappe.utils.add_to_date(now(), hours=-1)
        
        failed_syncs = frappe.get_all(
            "Salla Sync Log",
            filters={
                "status": "Failed",
                "started_at": [">", one_hour_ago]
            },
            fields=["name", "salla_store", "sync_type", "error_message", "started_at"]
        )
        
        if failed_syncs:
            # Send notification
            message = "<h3>Failed Salla Syncs (Last Hour)</h3><ul>"
            
            for sync in failed_syncs:
                message += f"""
                    <li>
                        <strong>{sync.sync_type}</strong> - {sync.salla_store}<br>
                        <small>Time: {sync.started_at}</small><br>
                        <small>Error: {sync.error_message[:200]}</small>
                    </li>
                """
            
            message += "</ul>"
            
            frappe.sendmail(
                recipients=[settings.notification_email],
                subject=f"Salla Integration: {len(failed_syncs)} Failed Syncs",
                message=message,
                delayed=False
            )
            
    except Exception as e:
        frappe.log_error(f"Failed to check sync status: {str(e)}", "Salla Check Task")


def generate_weekly_report():
    """
    Generate and send weekly sync report
    Runs weekly
    """
    from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import get_settings
    
    settings = get_settings()
    
    if not settings.notification_email:
        return
    
    try:
        # Get sync statistics for last 7 days
        week_ago = frappe.utils.add_to_date(now(), days=-7)
        
        stores = frappe.get_all(
            "Salla Store",
            filters={"is_authorized": 1},
            fields=["name", "store_name"]
        )
        
        report_html = "<h2>Salla Integration - Weekly Report</h2>"
        
        for store in stores:
            # Get sync logs for this store
            logs = frappe.get_all(
                "Salla Sync Log",
                filters={
                    "salla_store": store.name,
                    "started_at": [">", week_ago]
                },
                fields=["status", "sync_type", "successful_records", "failed_records"]
            )
            
            if not logs:
                continue
            
            # Calculate statistics
            total_syncs = len(logs)
            completed = len([l for l in logs if l.status == "Completed"])
            failed = len([l for l in logs if l.status == "Failed"])
            total_records = sum([l.successful_records or 0 for l in logs])
            failed_records = sum([l.failed_records or 0 for l in logs])
            
            report_html += f"""
                <h3>{store.store_name}</h3>
                <table border="1" cellpadding="5" style="border-collapse: collapse;">
                    <tr>
                        <td><strong>Total Syncs:</strong></td>
                        <td>{total_syncs}</td>
                    </tr>
                    <tr>
                        <td><strong>Completed:</strong></td>
                        <td style="color: green;">{completed}</td>
                    </tr>
                    <tr>
                        <td><strong>Failed:</strong></td>
                        <td style="color: red;">{failed}</td>
                    </tr>
                    <tr>
                        <td><strong>Records Synced:</strong></td>
                        <td>{total_records}</td>
                    </tr>
                    <tr>
                        <td><strong>Failed Records:</strong></td>
                        <td>{failed_records}</td>
                    </tr>
                </table>
                <br>
            """
        
        # Send email
        frappe.sendmail(
            recipients=[settings.notification_email],
            subject="Salla Integration - Weekly Report",
            message=report_html,
            delayed=False
        )
        
        frappe.logger().info("Sent Salla weekly report")
        
    except Exception as e:
        frappe.log_error(f"Failed to generate weekly report: {str(e)}", "Salla Report Task")