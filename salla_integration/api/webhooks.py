import json

import frappe
from frappe.utils import now_datetime
from erpnext.selling.doctype.sales_order.sales_order import (
    make_delivery_note,
    make_sales_invoice,
)
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
    get_settings,
)
from salla_integration.api.orders import (
    _append_order_level_charges,
    _apply_discount_from_amounts,
    _text,
    check_taxes,
    create_sales_order_from_salla,
    prepare_sales_order_item,
)
from salla_integration.utils.salla_client import SallaClient

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

    store_name = _resolve_store(parsed_payload, args)

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

    # Process only known order events; skip actions when store cannot be resolved
    try:
        if store_name and event_type == "order.status.updated":
            _handle_order_status_updated(store_name, event_type, parsed_payload)
        elif store_name and event_type == "order.updated":
            _handle_order_updated(store_name, event_type, parsed_payload)
    except Exception as exc:
        frappe.log_error(
            message=f"{exc}\n\nPayload:\n{json.dumps(parsed_payload, ensure_ascii=False) if parsed_payload else body}\n\nTraceback:\n{frappe.get_traceback()}",
            title="Salla Webhook Handler",
        )
        # Surface the exception text to the response for easier debugging
        return {
            "success": False,
            "error": str(exc),
            "traceback": frappe.get_traceback(),
        }

    return {"success": True}


def _resolve_store(payload: dict, args: dict) -> str:
    """
    Resolve Salla Store name from payload or query params.
    """
    merchant_id = _extract_merchant_id(payload)
    candidate = (
        args.get("store")
        or args.get("salla_store")
        or payload.get("store_name")
        or payload.get("salla_store")
    )
    if candidate and frappe.db.exists("Salla Store", candidate):
        return candidate

    if merchant_id:
        store = frappe.db.get_value("Salla Store", {"merchant_id": str(merchant_id)}, "name")
        if store:
            return store

    stores = frappe.get_all("Salla Store", filters={"is_authorized": 1}, fields=["name"])
    if len(stores) == 1:
        return stores[0]["name"]
    return ""


def _extract_merchant_id(payload: dict):
    """
    Safely extract merchant_id from payload where merchant may be int or dict.
    """
    merchant_field = payload.get("merchant")
    if isinstance(merchant_field, dict):
        return merchant_field.get("id")
    if isinstance(merchant_field, (int, str)):
        return merchant_field

    data = payload.get("data") or {}
    merchant_id = data.get("merchant_id") if isinstance(data, dict) else None
    if merchant_id:
        return merchant_id

    return payload.get("merchant_id")


def _get_order_id(event_type: str, payload: dict) -> str:
    """
    Normalize order ID using event type:
    - order.status.updated: data.order.id (or payload.order.id fallback)
    - order.updated / order.created: data.id
    - order.shipment.*: data.id
    - generic fallbacks: payload.order_id, payload.id
    """
    if not isinstance(payload, dict):
        return ""

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    order_block = data.get("order") if isinstance(data.get("order"), dict) else payload.get("order")

    order_id = None
    if event_type == "order.status.updated":
        order_id = (order_block or {}).get("id") if isinstance(order_block, dict) else None
    elif event_type in {"order.updated", "order.created", "order.shipment.creating", "order.shipment.created", "order.shipment.cancelled"}:
        order_id = data.get("id")

    if not order_id:
        order_id = (
            (order_block.get("id") if isinstance(order_block, dict) else None)
            or data.get("id")
            or payload.get("order_id")
            or payload.get("id")
        )

    return str(order_id) if order_id else ""


def _fetch_order_payload(store_name: str, order_id: str) -> dict:
    client = SallaClient(store_name)
    try:
        resp = client.get_order(order_id)
        if isinstance(resp, dict):
            return resp.get("data") or resp
    except Exception as exc:
        frappe.log_error(f"Failed to fetch order {order_id} for store {store_name}: {exc}", "Salla Webhook Order Fetch")
    return {}


def _get_status_doc(store_name: str, status_data: dict):
    status_id = _text(status_data.get("id"))
    slug = _text(status_data.get("slug") or status_data.get("type"))
    name = _text(status_data.get("name"))

    if status_id:
        docname = frappe.db.get_value(
            "Salla Order Status",
            {"salla_store": store_name, "salla_status_id": status_id},
            "name",
        )
        if docname:
            return frappe.get_doc("Salla Order Status", docname)

    if slug:
        docname = frappe.db.get_value(
            "Salla Order Status",
            {"salla_store": store_name, "slug": slug},
            "name",
        )
        if docname:
            return frappe.get_doc("Salla Order Status", docname)

    if name:
        docname = frappe.db.get_value(
            "Salla Order Status",
            {"salla_store": store_name, "status_name": name},
            "name",
        )
        if docname:
            return frappe.get_doc("Salla Order Status", docname)
    return None


def _find_sales_order(store_name: str, order_id: str) -> str:
    return (
        frappe.db.get_value(
            "Sales Order",
            {"salla_store": store_name, "salla_order_id": str(order_id)},
            "name",
        )
        or ""
    )


def _handle_order_status_updated(store_name: str, event_type: str, payload: dict):
    order_id = _get_order_id(event_type, payload)
    frappe.log_error(message=f"Order ID: {order_id} - {payload}",title= "Salla Webhook Order ID")
    if not order_id:
        return

    order_data = _fetch_order_payload(store_name, order_id)
    status_data = (order_data.get("status") if isinstance(order_data, dict) else None) or payload.get("status") or {}
    status_doc = _get_status_doc(store_name, status_data)
    if not status_doc:
        return

    sales_order_name = _find_sales_order(store_name, order_id)
    if not sales_order_name and status_doc.create_sales_order:
        sales_order_name = create_sales_order_from_salla(order_data or payload, store_name)

    if not sales_order_name:
        return

    so = frappe.get_doc("Sales Order", sales_order_name)

    if status_doc.submit_sales_order and so.docstatus == 0:
        so.submit()
        frappe.db.commit()

    if status_doc.cancel_sales_order and so.docstatus == 1:
        so.cancel()
        frappe.db.commit()
        return

    if status_doc.create_sales_invoice:
        _ensure_sales_invoice(so, submit=status_doc.submit_sales_invoice, cancel=status_doc.cancel_sales_invoice)

    if status_doc.create_delivery_note:
        _ensure_delivery_note(
            so,
            submit=status_doc.submit_sales_delivery_note,
            cancel=status_doc.cancel_delivery_note,
        )


def _ensure_sales_invoice(sales_order, submit: bool, cancel: bool):
    existing_parents = {
        row["parent"]
        for row in frappe.db.get_all(
            "Sales Invoice Item",
            filters={"sales_order": sales_order.name},
            fields=["parent"],
        )
    }
    invoices = [frappe.get_doc("Sales Invoice", name) for name in existing_parents] if existing_parents else []

    if not invoices:
        inv = make_sales_invoice(sales_order.name, ignore_permissions=True)
        inv.flags.ignore_permissions = True
        inv.insert(ignore_permissions=True)
        invoices = [inv]

    for inv in invoices:
        if cancel and inv.docstatus == 1:
            inv.cancel()
            frappe.db.commit()
            continue
        if submit and inv.docstatus == 0:
            inv.submit()
            frappe.db.commit()


def _ensure_delivery_note(sales_order, submit: bool, cancel: bool):
    existing_parents = {
        row["parent"]
        for row in frappe.db.get_all(
            "Delivery Note Item",
            filters={"against_sales_order": sales_order.name},
            fields=["parent"],
        )
    }
    notes = [frappe.get_doc("Delivery Note", name) for name in existing_parents] if existing_parents else []

    if not notes:
        # ensure SO is submitted before mapping
        if sales_order.docstatus == 0:
            try:
                sales_order.flags.ignore_permissions = True
                sales_order.submit()
                frappe.db.commit()
            except Exception as exc:
                frappe.log_error(
                    f"Failed to submit sales order {sales_order.name} before creating delivery note: {exc}\n{frappe.get_traceback()}",
                    "Salla Webhook Delivery Note Creation",
                )
                return

        prev_ignore = getattr(frappe.flags, "ignore_permissions", False)
        prev_user = frappe.session.user
        frappe.flags.ignore_permissions = True
        # Switch to Administrator to bypass role checks during mapping
        frappe.set_user("Administrator")
        try:
            dn = make_delivery_note(source_name=sales_order.name)
            if dn:
                dn.flags.ignore_permissions = True
                dn.insert(ignore_permissions=True)
                notes = [dn]
        except Exception as exc:
            frappe.log_error(
                f"Failed to create delivery note for sales order {sales_order.name}: {exc}\n{frappe.get_traceback()}",
                "Salla Webhook Delivery Note Creation",
            )
        finally:
            frappe.flags.ignore_permissions = prev_ignore
            frappe.set_user(prev_user)

    for dn in notes:
        if cancel and dn.docstatus == 1:
            dn.cancel()
            frappe.db.commit()
            continue
        if submit and dn.docstatus == 0:
            dn.submit()
            frappe.db.commit()


def _handle_order_updated(store_name: str, event_type: str, payload: dict):
    order_id = _get_order_id(event_type, payload)
    if not order_id:
        return

    sales_order_name = _find_sales_order(store_name, order_id)
    if not sales_order_name:
        return

    so = frappe.get_doc("Sales Order", sales_order_name)
    if so.docstatus != 0:
        return  # submitted or cancelled; skip

    order_data = _fetch_order_payload(store_name, order_id)
    if not order_data:
        return

    client = SallaClient(store_name)
    items = []
    try:
        resp = client.get_order_items(order_id)
        items = resp.get("data") or resp.get("items") or []
    except Exception:
        pass
    if not items:
        items = order_data.get("items") or []
    order_data["items"] = items

    _update_draft_sales_order(so, order_data, store_name)
    frappe.db.commit()


def _update_draft_sales_order(sales_order, order_data: dict, store_name: str):
    store = frappe.get_cached_doc("Salla Store", store_name)
    status_data = order_data.get("status") or {}
    payment_data = order_data.get("payment") or {}
    delivery_method = order_data.get("delivery_method")

    sales_order.po_no = order_data.get("reference_id", "")
    sales_order.salla_status_id = _text(status_data.get("id"))
    sales_order.salla_status_slug = status_data.get("slug") or status_data.get("type")
    sales_order.salla_status_name = status_data.get("name")
    sales_order.salla_payment_status = payment_data.get("status")
    sales_order.salla_payment_method = payment_data.get("method")
    sales_order.salla_delivery_method = delivery_method
    sales_order.salla_sync_status = "Synced"
    sales_order.salla_last_synced = now_datetime()

    sales_order.set("items", [])
    product_line_added = False
    for item_data in order_data.get("items") or []:
        row = prepare_sales_order_item(item_data, store_name)
        if row:
            sales_order.append("items", row)
            product_line_added = True
    if not product_line_added:
        return

    _append_order_level_charges(order_data, store, sales_order)
    _apply_discount_from_amounts(order_data, sales_order)
    check_taxes(store_name, str(order_data.get("id") or sales_order.salla_order_id), sales_order, order_data)

    if store.warehouse:
        stock_map = {}
        for item in sales_order.items:
            item_code = item.get("item_code")
            if not item_code:
                continue
            is_stock = stock_map.get(item_code)
            if is_stock is None:
                try:
                    is_stock = int(frappe.get_cached_value("Item", item_code, "is_stock_item") or 0)
                except Exception:
                    is_stock = 0
                stock_map[item_code] = is_stock
            if is_stock:
                item.warehouse = store.warehouse

    if store.price_list:
        sales_order.selling_price_list = store.price_list

    sales_order.save(ignore_permissions=True)
