import json

import frappe
from frappe.utils import now_datetime
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
    get_settings,
)

SALLA_WEBHOOK_EVENTS = [
    "order.created",
    "order.updated",
    "product.created",
    "product.updated",
    "product.deleted",
    "customer.created",
    "customer.updated",
    "category.created",
    "category.updated",
    "order.status.updated",
    "order.refunded",
    "order.cancelled",
    "unknown",
]


@frappe.whitelist(allow_guest=True)
def receive():
    """
    Generic webhook receiver for Salla webhooks.
    Stores the incoming request as a Salla Webhook Log.
    """
    # Allow incoming webhook calls without CSRF/session
    frappe.local.no_cache = 1
    frappe.local.flags.ignore_csrf = True

    settings = get_settings()
    if not getattr(settings, "enable_webhooks", 0):
        frappe.throw("Webhooks are disabled for this site.", frappe.PermissionError)

    body = frappe.request.get_data(as_text=True)
    headers = dict(frappe.request.headers or {})
    args = frappe.request.args or {}

    try:
        parsed_payload = json.loads(body) if body else {}
    except Exception:
        parsed_payload = {}

    raw_event_type = (
        frappe.get_request_header("X-Salla-Event")
        or parsed_payload.get("event")
        or args.get("event")
        or parsed_payload.get("type")
        or "unknown"
    )
    event_type = raw_event_type if raw_event_type in SALLA_WEBHOOK_EVENTS else "unknown"

    log = frappe.get_doc(
        {
            "doctype": "Salla Webhook Log",
            "event_type": event_type,
            "event_raw": raw_event_type,
            "payload": body,
            "status_code": 200,
            "headers": json.dumps(headers, ensure_ascii=False),
            "timestamp": now_datetime(),
            "request_method": getattr(frappe.request, "method", None),
            "request_url": getattr(frappe.request, "url", None),
            "remote_ip": getattr(frappe.request, "remote_addr", None),
            "signature": frappe.get_request_header("X-Salla-Signature")
            or frappe.get_request_header("X-Salla-Signature-V2"),
            "query_params": json.dumps(dict(args), ensure_ascii=False) if args else None,
        }
    )
    log.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True}

