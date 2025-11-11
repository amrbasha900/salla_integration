import frappe
import json
from frappe.utils import now
from typing import Any, Dict, List, Tuple
from erpnext.controllers.item_variant import create_variant, make_variant_item_code
from frappe.model.rename_doc import rename_doc
from salla_integration.utils.salla_client import SallaClient
from salla_integration.api.options import sync_product_options


def _text(value) -> str:
	try:
		if value is None:
			return ""
		return str(value).strip()
	except Exception:
		return ""

def _get_attr_names_for_product(salla_product: Dict[str, Any], store_name: str) -> List[str]:
	"""Resolve Item Attribute names for this product based on composed naming or Salla IDs."""
	template_sku = _text(salla_product.get("sku"))
	if not template_sku:
		return []
	attr_names: List[str] = []
	for opt in (salla_product.get("options") or []):
		opt_id = str(opt.get("id") or "")
		opt_name = _text(opt.get("name"))
		if not opt_id or not opt_name:
			continue
		composed = f"{template_sku} - {opt_id} - {opt_name}".strip()
		# Prefer composed attribute_name
		name = frappe.db.get_value("Item Attribute", {"attribute_name": composed}, "name")
		if not name:
			# Fallback to Salla IDs
			name = frappe.db.get_value(
				"Item Attribute",
				{"salla_option_id": opt_id, "salla_store": store_name, "product_sku": template_sku},
				"name"
			)
		if name:
			attr_names.append(name)
	# dedupe preserving order
	seen = set()
	ordered: List[str] = []
	for n in attr_names:
		if n not in seen:
			seen.add(n)
			ordered.append(n)
	return ordered


def _get_attr_names_from_erp(template_sku: str, store_name: str) -> List[str]:
	"""Fetch Item Attribute names from ERP by product_sku and store, independent of product payload."""
	if not template_sku:
		return []
	names: List[str] = []
	try:
		rows = frappe.get_all(
			"Item Attribute",
			filters={"product_sku": template_sku, "salla_store": store_name},
			fields=["attribute_name"],
			order_by="creation asc",
		)
		for r in rows:
			if r.get("attribute_name"):
				names.append(r.get("attribute_name"))
	except Exception:
		pass
	# dedupe preserve order
	seen = set()
	out: List[str] = []
	for n in names:
		if n not in seen:
			seen.add(n)
			out.append(n)
	return out

def _build_option_value_maps(full_product: Dict[str, Any], template_sku: str) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
	"""
	Returns:
	- option_id_to_attr_name: option_id -> composed attribute_name
	- value_id_to_value_label: value_id -> Item Attribute Value label
	- value_id_to_option_id: value_id -> option_id
	"""
	option_id_to_attr_name: Dict[str, str] = {}
	value_id_to_value_label: Dict[str, str] = {}
	value_id_to_option_id: Dict[str, str] = {}
	for opt in (full_product.get("options") or []):
		opt_id = str(opt.get("id") or "")
		opt_name = _text(opt.get("name"))
		if not opt_id or not opt_name:
			continue
		attr_name = f"{template_sku} - {opt_id} - {opt_name}".strip()
		option_id_to_attr_name[opt_id] = attr_name
		for v in (opt.get("values") or []):
			vid = str(v.get("id") or "")
			if not vid:
				continue
			label = _text(v.get("name")) or _text(v.get("display_value")) or _text(v.get("hashed_display_value")) or vid
			value_id_to_value_label[vid] = label
			value_id_to_option_id[vid] = opt_id
	return option_id_to_attr_name, value_id_to_value_label, value_id_to_option_id

def _build_maps_from_erp(template_sku: str, store_name: str) -> Dict[str, Tuple[str, str]]:
	"""
	Map salla_option_value_id -> (attribute_name, attribute_value_label) using ERP Item Attribute data.
	"""
	value_to_attr: Dict[str, Tuple[str, str]] = {}
	try:
		attr_rows = frappe.get_all(
			"Item Attribute",
			filters={"product_sku": template_sku, "salla_store": store_name},
			fields=["name", "attribute_name"],
		)
		for r in attr_rows:
			try:
				doc = frappe.get_doc("Item Attribute", r["name"])
			except Exception:
				continue
			attr_name = doc.attribute_name
			for v in (doc.item_attribute_values or []):
				vid = getattr(v, "salla_option_value_id", None)
				lbl = getattr(v, "attribute_value", None)
				if vid and lbl:
					value_to_attr[str(vid)] = (attr_name, lbl)
	except Exception:
		pass
	return value_to_attr


def create_variants_from_api(template_item, salla_product: Dict[str, Any], sync_log=None):
	"""Create ERPNext Item Variants from Salla variants endpoint."""
	product_id = str(salla_product.get("id") or "")
	store_name = _text(template_item.get("salla_store"))
	template_sku = _text(template_item.get("item_code"))
	if not store_name:
		log_sync(sync_log, "SKIP - no store", product_id, "", template_item.name, "Template missing salla_store")
		return

	client = SallaClient(store_name)
	# Get full product to ensure options/values present for mapping
	try:
		full_product = client.get_product(product_id) or salla_product
	except Exception:
		full_product = salla_product

	option_id_to_attr_name, value_id_to_value_label, value_id_to_option_id = _build_option_value_maps(full_product, template_sku)
	erp_value_to_attr = _build_maps_from_erp(template_sku, store_name)

	# Iterate Salla variants
	for variant_data in client.iterate_pages("get_product_variants", product=product_id):
		sku = _text(variant_data.get("sku"))
		if not sku:
			log_sync(sync_log, "SKIP - missing SKU", product_id, "", "", "Variant with empty SKU")
			continue
		# Skip if already exists
		if frappe.db.exists("Item", {"item_code": sku}):
			log_sync(sync_log, "SKIP - item exists", product_id, sku, sku, "Variant already exists")
			continue

		# Map related_option_values -> args {attribute_name: attribute_value_label}
		args: Dict[str, str] = {}
		for vid in (variant_data.get("related_option_values") or []):
			vid_str = str(vid)
			# Prefer ERP mapping to guarantee exact attribute_name matching
			if vid_str in erp_value_to_attr:
				attr_name, val_label = erp_value_to_attr[vid_str]
			else:
				attr_name = option_id_to_attr_name.get(value_id_to_option_id.get(vid_str, ""))
				val_label = value_id_to_value_label.get(vid_str)
			if attr_name and val_label:
				args[attr_name] = val_label
		if not args:
			log_sync(sync_log, "SKIP - no attributes", product_id, sku, "", "No attribute mapping for variant")
			continue

		log_sync(sync_log, "ATTEMPT - variant create", product_id, sku, "", f"Attr Map: {args}")
		try:
			# Create variant using standard API
			variant = create_variant(template_item.name, args)
			# Set item_code to Salla SKU
			old_name = variant.name
			try:
				variant.item_code = sku
			except Exception:
				pass
			# Set Salla custom fields on variant
			for field, value in [
				("salla_is_from_salla", 1),
				("salla_store", store_name),
				("salla_product_id", product_id),
				("salla_sku", sku),
				("salla_option_ids", json.dumps(variant_data.get("related_options") or [])),
				("salla_option_value_ids", json.dumps(variant_data.get("related_option_values") or [])),
			]:
				if field in variant.as_dict():
					try:
						variant.set(field, value)
					except Exception:
						pass
			variant.save(ignore_permissions=True)
			frappe.db.commit()

			# Rename to SKU if needed
			if variant.name != sku:
				try:
					rename_doc("Item", variant.name, sku, force=True, merge=False)
					variant = frappe.get_doc("Item", sku)
					log_sync(sync_log, "RENAMED - variant", product_id, sku, variant.name, f"Renamed from {old_name} to {sku}")
				except Exception as e:
					log_sync(sync_log, "WARN - rename failed", product_id, sku, old_name, f"{e}")
			log_sync(sync_log, "CREATED - variant", product_id, sku, variant.name, f"Attributes: {args}")
		except Exception as e:
			msg = str(e)
			log_sync(sync_log, "ERROR - variant", product_id, sku, "", msg)
			frappe.log_error("Salla Variant Creation", f"Failed to create variant for SKU {sku}: {msg}")

# Legacy helpers removed; options flow handles attribute creation


def log_sync(sync_log, action: str, salla_id: str = "", salla_sku: str = "", erp_item_code: str = "", message: str = ""):
	"""Append a structured entry to Salla Sync Log.sync_data and update counters."""
	entry = {
		"ts": now(),
		"action": action,
		"salla_product_id": salla_id,
		"salla_sku": salla_sku,
		"erp_item_code": erp_item_code,
		"message": message
	}
	try:
		# sync_data is JSON text; keep small to avoid version conflicts
		data = {}
		if sync_log.sync_data:
			import json as _json
			data = _json.loads(sync_log.sync_data)
		entries = data.get("entries", [])
		entries.append(entry)
		data["entries"] = entries[-500:]  # cap to last 500
		sync_log.sync_data = _json.dumps(data, indent=2, ensure_ascii=False)
		sync_log.save(ignore_permissions=True)
	except Exception:
		# Fallback to error_message aggregation
		try:
			existing = sync_log.error_message or ""
			line = f"[{entry['ts']}] {action} | salla:{salla_id} sku:{salla_sku} erp:{erp_item_code} - {message}"
			frappe.db.set_value("Salla Sync Log", sync_log.name, "error_message", (existing + "\n" + line).strip())
		except Exception:
			pass


def ensure_template_item(salla_product: Dict[str, Any], store_name: str, sync_log=None):
	"""Ensure ERPNext template Item exists. Respect naming; lookup by product SKU when present."""
	product_id = str(salla_product.get("id") or "")
	template_sku = _text(salla_product.get("sku"))

	# If SKU exists and Item with same item_code exists, reuse it
	if template_sku and frappe.db.exists("Item", {"item_code": template_sku}):
		item = frappe.get_doc("Item", template_sku)
		log_sync(sync_log, "SKIP - item exists", product_id, template_sku, item.name, "Template item already exists")
		return item

	# Create new template item (let ERPNext naming rules apply when no SKU)
	item = frappe.new_doc("Item")
	if template_sku:
		item.item_code = template_sku
	item.item_name = _text(salla_product.get("name")) or template_sku or f"Salla-{product_id}"
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	# Attach attributes (ensured by options flow) before insert.
	# Prefer product payload; fall back to ERP lookup if payload lacks options.
	names = _get_attr_names_for_product(salla_product, store_name)
	if not names:
		names = _get_attr_names_from_erp(template_sku, store_name)
	for attr_name in names:
		item.append("attributes", {"attribute": attr_name})
	item.has_variants = 1 if names else 0
	# Salla custom fields if present in system
	for field, value in [
		("salla_is_from_salla", 1),
		("salla_store", store_name),
		("salla_product_id", product_id),
		("salla_sku", template_sku),
	]:
		if field in item.as_dict():
			item.set(field, value)
	item.insert(ignore_permissions=True)
	frappe.db.commit()
	log_sync(sync_log, "CREATED - template", product_id, template_sku, item.name, "Template item created")
	return item


# Legacy helper removed


def ensure_attributes_from_options(*args, **kwargs):
	"""Deprecated: handled by options flow; left for compatibility."""
	return


def create_salla_option_records(*args, **kwargs):
	"""Deprecated: options flow persists Salla Product Option docs."""
	return


 


def sync_salla_product(store_name: str, salla_product: Dict[str, Any], sync_log=None):
	"""Main entry to sync one Salla product with options/variants into ERPNext."""
	product_id = str(salla_product.get("id") or "")
	# Skip when no SKU at all (template and variant) per rules: if template SKU missing but skus have, proceed on variants
	template_sku = (salla_product.get("sku") or "").strip() if salla_product.get("sku") else ""
	if not template_sku and not (salla_product.get("skus") or []):
		log_sync(sync_log, "SKIP - missing SKU", product_id, "", "", "No template SKU and no variant SKUs")
		return
	# First ensure options and Item Attributes exist for this product
	try:
		if salla_product:
			sync_product_options(store_name, salla_product, sync_log_name=(sync_log.name if sync_log else None))
	except Exception as e:
		frappe.log_error("Salla Product Flow", f"Options pre-sync failed for product {product_id}: {e}")
	# Ensure template with attributes attached
	template_item = ensure_template_item(salla_product, store_name, sync_log=sync_log)
	# Create variants from Salla API
	create_variants_from_api(template_item, salla_product, sync_log=sync_log)

