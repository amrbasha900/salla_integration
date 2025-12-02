import json
import traceback
from typing import Dict, Optional

import frappe

from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
	get_settings,
)
from salla_integration.salla_integration.doctype.salla_sync_log.salla_sync_log import (
	create_sync_log,
)
from salla_integration.utils.salla_client import SallaClient


def sync_brands(store_name: str, sync_log_name: Optional[str] = None) -> Dict[str, int]:
	"""
	Sync all brands from Salla to ERPNext Brand doctype.
	"""
	if sync_log_name:
		sync_log = frappe.get_doc("Salla Sync Log", sync_log_name)
	else:
		sync_log = create_sync_log(store_name, "Brands")

	try:
		client = SallaClient(store_name)
		settings = get_settings()
		total = 0
		batch_counter = 0
		per_page = min(settings.batch_size or client.MAX_PER_PAGE, client.MAX_PER_PAGE)

		for brand in client.iterate_pages(
			"get_brands", per_page=per_page, include_translations=True
		):
			total += 1
			batch_counter += 1
			try:
				upsert_brand(store_name, brand)
				sync_log.increment_success()
			except Exception as exc:
				frappe.log_error(
					"Salla Brand Sync",
					f"Failed to sync brand {brand.get('id')} for store {store_name}: {exc}",
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
		sync_log.log_failure(str(exc), traceback.format_exc())
		raise


def upsert_brand(store_name: str, brand_data: Dict) -> Optional[str]:
	"""
	Create or update a Brand record from Salla payload.
	"""
	if not brand_data:
		return None

	salla_id = str(brand_data.get("id") or "").strip()
	if not salla_id:
		return None

	brand_name = (brand_data.get("name") or f"Salla Brand {salla_id}").strip()
	metadata = brand_data.get("metadata") or {}
	translations = brand_data.get("translations") or {}

	fields = {
		"brand": brand_name,
		"description": brand_data.get("description") or "",
		"salla_brand_id": salla_id,
		"salla_is_from_salla": 1,
		"salla_store": store_name,
		"salla_logo_url": brand_data.get("logo") or "",
		"salla_banner_url": brand_data.get("banner") or "",
		"salla_metadata_title": metadata.get("title") or "",
		"salla_metadata_description": metadata.get("description") or "",
		"salla_metadata_url": metadata.get("url") or "",
		"salla_ar_char": brand_data.get("ar_char") or "",
		"salla_en_char": brand_data.get("en_char") or "",
		"salla_translations": json.dumps(
			translations, ensure_ascii=False, separators=(",", ":")
		)
		if translations
		else "",
	}

	existing = frappe.db.get_value(
		"Brand", {"salla_brand_id": salla_id, "salla_store": store_name}, "name"
	)

	if existing:
		doc = frappe.get_doc("Brand", existing)
		changed = False
		for fieldname, value in fields.items():
			if doc.get(fieldname) != value:
				doc.set(fieldname, value)
				changed = True
		if changed:
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		return doc.name

	fields.update({"doctype": "Brand"})

	doc = frappe.get_doc(fields)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name

