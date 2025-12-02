import json
import traceback
from typing import Any, Dict, List, Optional

import frappe

from salla_integration.salla_integration.doctype.salla_sync_log.salla_sync_log import (
	create_sync_log,
)
from salla_integration.utils.salla_client import SallaClient


def sync_order_statuses(store_name: str, sync_log_name: Optional[str] = None) -> Dict[str, Any]:
	"""
	Sync order status definitions from Salla into the Salla Order Status doctype.
	"""
	if sync_log_name:
		sync_log = frappe.get_doc("Salla Sync Log", sync_log_name)
	else:
		sync_log = create_sync_log(store_name, "Order Statuses")

	try:
		client = SallaClient(store_name)
		response = client.get_order_statuses()
		statuses = _extract_statuses(response)

		total = 0
		batch_counter = 0
		for status_payload in statuses:
			total += 1
			batch_counter += 1
			try:
				upsert_order_status(store_name, status_payload)
				sync_log.increment_success()
			except Exception as exc:  # pragma: no cover - defensive logging
				frappe.log_error(
					"Salla Order Status Sync",
					f"Failed to sync order status {status_payload.get('id')} "
					f"for store {store_name}: {exc}",
				)
				sync_log.increment_failed()

			if batch_counter >= 50:
				sync_log.total_records = total
				sync_log.save()
				frappe.db.commit()
				batch_counter = 0

		sync_log.total_records = total
		sync_log.save()
		sync_log.log_success()

		return {
			"success": True,
			"total": total,
		}

	except Exception as exc:
		sync_log.log_failure(str(exc), traceback.format_exc())
		raise


def upsert_order_status(store_name: str, status_data: Dict[str, Any]) -> Optional[str]:
	"""
	Create or update a Salla Order Status record from a Salla payload.
	"""
	if not status_data:
		return None

	salla_status_id = str(status_data.get("id") or "").strip()
	if not salla_status_id:
		return None

	status_name = (status_data.get("name") or salla_status_id).strip()
	translations_payload = status_data.get("translations")
	translations_json = ""

	if translations_payload:
		try:
			translations_json = json.dumps(
				translations_payload, ensure_ascii=False, separators=(",", ":")
			)
		except TypeError:
			translations_json = frappe.as_json(translations_payload, indent=0)

	parent_info = status_data.get("parent") or {}
	original_info = status_data.get("original") or {}
	sort_value = status_data.get("sort")
	if sort_value is None:
		sort_value = status_data.get("sort_order")
	sort_value = int(sort_value or 0)

	fields = {
		"salla_store": store_name,
		"salla_status_id": salla_status_id,
		"status_name": status_name,
		"status_type": status_data.get("type") or "",
		"slug": status_data.get("slug") or "",
		"sort_order": sort_value,
		"icon": status_data.get("icon") or "",
		"is_active": 1 if status_data.get("is_active") else 0,
		"message": status_data.get("message") or "",
		"translations_json": translations_json,
		"parent_status_id": str(parent_info.get("id") or "") if parent_info else "",
		"parent_status_name": parent_info.get("name") or "" if parent_info else "",
		"original_status_id": str(original_info.get("id") or "") if original_info else "",
		"original_status_name": original_info.get("name") or "" if original_info else "",
	}

	existing = frappe.db.get_value(
		"Salla Order Status",
		{"salla_store": store_name, "salla_status_id": salla_status_id},
		"name",
	)

	if existing:
		doc = frappe.get_doc("Salla Order Status", existing)
		changed = False
		for fieldname, value in fields.items():
			if doc.get(fieldname) != value:
				doc.set(fieldname, value)
				changed = True
		if changed:
			doc.save(ignore_permissions=True)
			frappe.db.commit()

		expected_name = f"{salla_status_id}-{status_name}"
		if expected_name and doc.name != expected_name:
			try:
				frappe.rename_doc("Salla Order Status", doc.name, expected_name, force=True, merge=False)
				return expected_name
			except Exception:
				pass
		return doc.name

	fields.update({"doctype": "Salla Order Status"})
	doc = frappe.get_doc(fields)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def _extract_statuses(response: Any) -> List[Dict[str, Any]]:
	"""
	Normalize response payloads into a list of status dictionaries.
	"""
	if response is None:
		return []

	if isinstance(response, list):
		return response

	if isinstance(response, dict):
		data = response.get("data")
		if isinstance(data, list):
			return data
		statuses = response.get("statuses")
		if isinstance(statuses, list):
			return statuses

	return []

