import traceback
from typing import Dict, List, Optional, Union

import frappe
from frappe.utils import cint

from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
	get_settings,
)
from salla_integration.salla_integration.doctype.salla_sync_log.salla_sync_log import (
	create_sync_log,
)
from salla_integration.utils.salla_client import SallaClient

STORE_CUSTOMER_GROUP_PARENT = "All Customer Groups"


def ensure_store_customer_group(store_name: str) -> str:
	"""
	Ensure a top-level Customer Group exists for a Salla store.

	Args:
	    store_name: Name of the Salla Store doc.
	Returns:
	    Customer Group name that acts as the root for this store.
	"""
	group_name = frappe.db.exists("Customer Group", store_name)

	if not group_name:
		# Attempt to find any group that matches customer_group_name (could differ from docname)
		group_name = frappe.db.get_value(
			"Customer Group",
			{"customer_group_name": store_name},
			"name",
		)

	if group_name:
		doc = frappe.get_doc("Customer Group", group_name)
		changed = False
		if not cint(doc.is_group):
			doc.is_group = 1
			changed = True
		if doc.parent_customer_group != STORE_CUSTOMER_GROUP_PARENT:
			doc.parent_customer_group = STORE_CUSTOMER_GROUP_PARENT
			changed = True
		if changed:
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		return doc.name

	# Create new store root group
	doc = frappe.get_doc(
		{
			"doctype": "Customer Group",
			"customer_group_name": store_name,
			"parent_customer_group": STORE_CUSTOMER_GROUP_PARENT,
			"is_group": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def find_customer_group_by_salla_id(
	salla_group_id: str, parent_customer_group: Optional[str] = None
) -> Optional[str]:
	if not salla_group_id:
		return None

	filters: Dict[str, Union[str, List[str]]] = {"salla_customer_id": salla_group_id}
	if parent_customer_group:
		filters["parent_customer_group"] = parent_customer_group

	return frappe.db.get_value("Customer Group", filters, "name")


def upsert_customer_group(
	store_name: str,
	group_payload: Union[Dict, str, int],
	parent_customer_group: Optional[str] = None,
) -> Optional[str]:
	"""
	Create or update a Customer Group based on Salla payload.

	Args:
	    store_name: Salla Store name.
	    group_payload: Raw payload from Salla (dict/str/int).
	    parent_customer_group: Optional parent group name. Defaults to store root.
	"""
	group = _normalize_group_payload(group_payload)
	salla_id = group.get("salla_id")
	if not salla_id:
		return None

	parent_customer_group = parent_customer_group or ensure_store_customer_group(store_name)
	existing_name = find_customer_group_by_salla_id(salla_id, parent_customer_group)
	if not existing_name:
		# Fallback: locate anywhere to maintain backwards compatibility
		existing_name = frappe.db.get_value(
			"Customer Group", {"salla_customer_id": salla_id}, "name"
		)

	fields_to_update = {
		"customer_group_name": group.get("name"),
		"parent_customer_group": parent_customer_group,
		"is_group": 0,
		"description": group.get("description", ""),
		"salla_customer_id": salla_id,
	}

	if existing_name:
		doc = frappe.get_doc("Customer Group", existing_name)
		changed = False
		for fieldname, value in fields_to_update.items():
			if doc.get(fieldname) != value:
				doc.set(fieldname, value)
				changed = True
		if changed:
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		return doc.name

	doc = frappe.get_doc(
		{
			"doctype": "Customer Group",
			**fields_to_update,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def sync_customer_groups(store_name: str, sync_log_name: Optional[str] = None):
	"""
	Fetch all customer groups from Salla and upsert them in ERPNext.
	"""
	if sync_log_name:
		sync_log = frappe.get_doc("Salla Sync Log", sync_log_name)
	else:
		sync_log = create_sync_log(store_name, "Customer Groups")

	try:
		settings = get_settings()
		client = SallaClient(store_name)
		parent = ensure_store_customer_group(store_name)
		total = 0
		batch_counter = 0
		per_page = min(settings.batch_size or 60, client.MAX_PER_PAGE)

		for group in client.iterate_pages("get_customer_groups", per_page=per_page):
			total += 1
			batch_counter += 1
			try:
				upsert_customer_group(store_name, group, parent_customer_group=parent)
				sync_log.increment_success()
			except Exception as exc:
				frappe.log_error(
					"Salla Customer Group Sync",
					f"Failed to sync customer group {group.get('id')}: {exc}",
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
		return {"success": True, "total": total}

	except Exception as exc:
		error_msg = str(exc)
		tb = traceback.format_exc()
		sync_log.log_failure(error_msg, tb)
		raise


def resolve_customer_groups(
	store_name: str, raw_groups: Optional[List[Union[Dict, str, int]]]
) -> List[str]:
	"""
	Resolve Salla group payloads to ERPNext Customer Group names, creating any missing ones.
	"""
	if not raw_groups:
		return []

	parent = ensure_store_customer_group(store_name)
	resolved: List[str] = []

	for payload in raw_groups:
		group = _normalize_group_payload(payload)
		salla_id = group.get("salla_id")
		if not salla_id:
			continue
		group_name = find_customer_group_by_salla_id(salla_id, parent) or upsert_customer_group(
			store_name, group, parent_customer_group=parent
		)
		if group_name and group_name not in resolved:
			resolved.append(group_name)

	return resolved


def _normalize_group_payload(payload: Union[Dict, str, int]) -> Dict[str, Optional[str]]:
	if isinstance(payload, (str, int)):
		return {
			"salla_id": str(payload),
			"name": str(payload),
			"description": "",
		}

	if not isinstance(payload, dict):
		return {"salla_id": None, "name": None, "description": None}

	salla_id = payload.get("id") or payload.get("group_id")
	name = payload.get("name") or payload.get("title") or str(salla_id or "") or None
	description = payload.get("description") or payload.get("note") or ""

	return {
		"salla_id": str(salla_id) if salla_id else None,
		"name": name or (f"Salla Group {salla_id}" if salla_id else None),
		"description": description,
	}

