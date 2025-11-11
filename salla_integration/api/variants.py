import frappe
from frappe.utils import now
from typing import Any, Dict, List, Tuple
from erpnext.controllers.item_variant import create_variant, make_variant_item_code
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

def ensure_attribute_defs_and_values(salla_product: Dict[str, Any], store_name: str, sync_log=None) -> List[str]:
	"""
	Ensure Item Attribute docs (and their values) exist for all Salla options.
	Returns list of Item Attribute names to attach to the template.
	"""
	attr_names: List[str] = []
	for opt in (salla_product.get("options") or []):
		attr_name = _text(opt.get("name"))
		if not attr_name:
			continue
		# Ensure Item Attribute exists
		try:
			docname = frappe.db.get_value("Item Attribute", {"attribute_name": attr_name}, "name")
			if not docname:
				attr = frappe.get_doc({
					"doctype": "Item Attribute",
					"attribute_name": attr_name,
					"numeric_values": 0
				})
				attr.insert(ignore_permissions=True)
				docname = attr.name
				log_sync(sync_log, "CREATED - attribute", str(salla_product.get("id") or ""), "", "", f"{attr_name}")
			# Ensure values
			attr_doc = frappe.get_doc("Item Attribute", docname)
			existing = {v.attribute_value for v in (attr_doc.item_attribute_values or [])}
			changed = False
			for val in (opt.get("values") or []):
				val_label = _text(val.get("name")) or _text(val.get("display_value")) or _text(val.get("hashed_display_value")) or str(val.get("id") or "")
				if not val_label or val_label in existing:
					continue
				attr_doc.append("item_attribute_values", {"attribute_value": val_label, "abbr": val_label})
				existing.add(val_label)
				changed = True
			if changed:
				attr_doc.save(ignore_permissions=True)
			attr_names.append(docname)
		except Exception as e:
			frappe.log_error("Salla Attribute Ensure", f"Failed to ensure attribute {attr_name}: {e}")
	return list(dict.fromkeys(attr_names))


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
	# Attach attributes (ensured by options flow) before insert
	options = salla_product.get("options") or []
	if options:
		for attr_name in _get_attr_names_for_product(salla_product, store_name):
			item.append("attributes", {"attribute": attr_name})
		item.has_variants = 1
	else:
		item.has_variants = 0
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


def _collect_option_maps(salla_product: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
	"""
	Map Salla option/value IDs to ERPNext attribute/value labels.
	Returns:
	- option_id_to_attr_name
	- value_id_to_value_label
	"""
	option_id_to_attr_name: Dict[str, str] = {}
	value_id_to_value_label: Dict[str, str] = {}
	for opt in salla_product.get("options") or []:
		attr_name = _text(opt.get("name"))
		option_id = str(opt.get("id") or "")
		if not attr_name or not option_id:
			continue
		option_id_to_attr_name[option_id] = attr_name
		for val in opt.get("values") or []:
			# Prefer value 'name' to match ERPNext Item Attribute Value labels
			value_label = _text(val.get("name")) or _text(val.get("display_value")) or _text(val.get("hashed_display_value"))
			value_id = str(val.get("id") or "")
			if value_label and value_id:
				value_id_to_value_label[value_id] = value_label
	return option_id_to_attr_name, value_id_to_value_label


def ensure_attributes_from_options(salla_product: Dict[str, Any], template_item, sync_log=None):
	"""Create Item Attribute and Values, and attach attributes to template item."""
	product_id = str(salla_product.get("id") or "")
	options = salla_product.get("options") or []
	if not options:
		return
	# Consider only options that actually participate in variants (appear in skus.related_option_values)
	used_value_ids = set()
	for sku_row in (salla_product.get("skus") or []):
		for vid in (sku_row.get("related_option_values") or []):
			try:
				used_value_ids.add(str(vid))
			except Exception:
				continue

	attached_attrs = set([row.attribute for row in (template_item.get("attributes") or []) if row.attribute])

	for opt in options:
		attr_name = _text(opt.get("name"))
		if not attr_name:
			continue
		# Skip options with no values or not used in any SKU combinations
		values = opt.get("values") or []
		if used_value_ids:
			has_used = any(str(v.get("id") or "") in used_value_ids for v in values)
			if not has_used:
				continue
		# Ensure Item Attribute
		attr_doc = frappe.db.get_value("Item Attribute", {"attribute_name": attr_name}, "name")
		if not attr_doc:
			attr = frappe.get_doc({"doctype": "Item Attribute", "attribute_name": attr_name, "numeric_values": 0})
			attr.insert(ignore_permissions=True)
			attr_doc = attr.name
			log_sync(sync_log, "CREATED - attribute", product_id, "", "", f"Attribute {attr_name}")
		# Ensure attribute values
		existing_vals = {
			v.attribute_value: 1 for v in (frappe.get_doc("Item Attribute", attr_doc).get("item_attribute_values") or [])
		}
		changed = False
		attr_inst = frappe.get_doc("Item Attribute", attr_doc)
		for val in values:
			# Only include values that are used in SKUs (if we know them)
			val_id = str(val.get("id") or "")
			if used_value_ids and val_id not in used_value_ids:
				continue
			value_label = _text(val.get("display_value")) or _text(val.get("name"))
			if not value_label or value_label in existing_vals:
				continue
			attr_inst.append("item_attribute_values", {"attribute_value": value_label})
			changed = True
			log_sync(sync_log, "CREATED - attribute value", product_id, "", "", f"{attr_name}={value_label}")
		if changed:
			attr_inst.save(ignore_permissions=True)
		# Attach to template attributes table
		if attr_doc not in attached_attrs:
			template_item.append("attributes", {"attribute": attr_doc})
			attached_attrs.add(attr_doc)
	template_item.has_variants = 1
	template_item.save(ignore_permissions=True)
	frappe.db.commit()


def create_salla_option_records(salla_product: Dict[str, Any], template_item, sync_log=None):
	"""Create Salla Product Option records linked to template item, named {item_sku}-{option_id}."""
	product_id = str(salla_product.get("id") or "")
	template_sku = _text(template_item.item_code)
	for opt in salla_product.get("options") or []:
		option_id = str(opt.get("id") or "")
		if not option_id:
			continue
		# Name pattern ensured by autoname; ensure unique per (erp_item_code, option_id)
		exists = frappe.db.exists("Salla Product Option", {"erp_item_code": template_item.name, "option_id": option_id})
		if exists:
			continue
		doc = frappe.get_doc({
			"doctype": "Salla Product Option",
			"salla_store": template_item.get("salla_store"),
			"product_id": product_id,
			"option_id": option_id,
			"option_name": _text(opt.get("name")),
			"option_type": opt.get("type") or "",
			"required": int(bool(opt.get("required"))),
			"display_type": opt.get("display_type") or "",
			"visibility": opt.get("visibility") or "",
			"erp_item_code": template_item.name
		})
		# Append values' labels
		for val in opt.get("values") or []:
			doc.append("values", {
				"value_id": str(val.get("id") or ""),
				"value_name": _text(val.get("name")),
				"display_value": _text(val.get("display_value"))
			})
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		log_sync(sync_log, "CREATED - option record", product_id, template_sku, template_item.name, f"Option {option_id}")


def create_variants_from_api(template_item, salla_product: Dict[str, Any], sync_log=None):
	"""Create ERPNext variants by fetching Salla variants via API (not from product.skus)."""
	product_id = str(salla_product.get("id") or "")
	if not product_id:
		return
	store_name = _text(template_item.get("salla_store"))
	if not store_name:
		log_sync(sync_log, "SKIP - no store", product_id, "", template_item.name, "Template missing salla_store")
		return
	client = SallaClient(store_name)
	# Fetch full product to ensure options are present for mapping
	try:
		full_product = client.get_product(product_id) or {}
	except Exception as e:
		full_product = {}
		frappe.log_error("Salla Variant Fetch", f"Failed to fetch full product {product_id}: {e}")
	# Ensure attributes exist and attached based on full product options
	try:
		ensure_attributes_from_options(full_product or salla_product, template_item, sync_log=sync_log)
	except Exception:
		pass
	# Build value_id -> label map from product options
	_, value_id_to_label = _collect_option_maps(full_product or salla_product)
	# Iterate Salla API variants pages
	for variant_row in client.iterate_pages("get_product_variants", product=product_id, per_page=60):
		frappe.log_error("Salla Variant Fetch", f"Variant row: {variant_row}")
		if not isinstance(variant_row, dict):
			continue
		erp_sku = _text(variant_row.get("sku"))
		if not erp_sku:
			log_sync(sync_log, "SKIP - missing SKU", product_id, "", "", "Variant with empty SKU")
			continue
		# Ensure template has attributes table populated before variant creation
		try:
			template_item.reload()
		except Exception:
			pass
		if not (template_item.get("attributes") or []):
			# Try to attach now
			try:
				ensure_attributes_from_options(salla_product, template_item, sync_log=sync_log)
			except Exception:
				pass
		# Double-check via DB child table count
		attr_rows = frappe.db.count("Item Variant Attribute", {"parent": template_item.name})
		if not (template_item.get("attributes") or []) and not attr_rows:
			log_sync(sync_log, "SKIP - template missing attributes", product_id, erp_sku, template_item.name, "Attribute table is mandatory")
			continue
		# Skip if exists
		if frappe.db.exists("Item", {"item_code": erp_sku}):
			log_sync(sync_log, "SKIP - item exists", product_id, erp_sku, erp_sku, "Variant already exists")
			continue
		# Build attribute map from related_option_values
		attr_values: Dict[str, str] = {}
		for val_id in (variant_row.get("related_option_values") or []):
			key = str(val_id)
			label = value_id_to_label.get(key)
			if not label:
				continue
			# We must resolve which attribute this value belongs to; derive from options
			for opt in (full_product.get("options") if isinstance(full_product, dict) else []) or (salla_product.get("options") or []):
				if any(str(v.get("id")) == key for v in (opt.get("values") or [])):
					attr_name = _text(opt.get("name"))
					if attr_name and label:
						attr_values[attr_name] = label
					break
		if not attr_values:
			log_sync(sync_log, "SKIP - no attributes", product_id, erp_sku, "", "No attribute mapping for variant")
			continue
		# Log attempt for observability
		log_sync(sync_log, "ATTEMPT - variant create", product_id, erp_sku, template_item.name, f"Attr Map: {attr_values}")
		# Create variant via ERPNext API
		try:
			variant = create_variant(template_item.name, {"attributes": [{"attribute": k, "attribute_value": v} for k, v in attr_values.items()]})
			frappe.db.commit()
			frappe.log_error("Salla Variant Create", f"Variant created: {variant.name}")
			# Respect ERPNext naming; set item_code to Salla SKU if permitted
			try:
				variant.item_code = erp_sku
			except Exception:
				pass
			# Set Salla fields if present
			for field, value in [
				("salla_store", template_item.get("salla_store")),
				("salla_product_id", product_id),
				("salla_sku", erp_sku),
			]:
				if field in variant.as_dict():
					try:
						variant.set(field, value)
					except Exception:
						pass
			variant.save(ignore_permissions=True)
			# Rename to SKU if ERPNext naming prevented direct item_code set
			try:
				if erp_sku and variant.name != erp_sku:
					from frappe.model.rename_doc import rename_doc
					rename_doc("Item", variant.name, erp_sku, force=True, merge=False)
					variant = frappe.get_doc("Item", erp_sku)
			except Exception as e_rename:
				frappe.log_error("Salla Variant Rename", f"Rename to SKU failed for {variant.name} -> {erp_sku}: {e_rename}")
			frappe.db.commit()
			log_sync(sync_log, "CREATED - variant", product_id, erp_sku, variant.name, f"Attributes: {attr_values}")
		except Exception as e:
			msg = str(e)
			# Gracefully handle missing attribute table by ensuring attributes and skipping if still missing
			if "Attribute table is mandatory" in msg:
				try:
					ensure_attributes_from_options(salla_product, template_item, sync_log=sync_log)
					template_item.reload()
				except Exception:
					pass
				attr_rows = frappe.db.count("Item Variant Attribute", {"parent": template_item.name})
				if not (template_item.get("attributes") or []) and not attr_rows:
					log_sync(sync_log, "SKIP - template missing attributes", product_id, erp_sku, template_item.name, "Attribute table is mandatory")
					continue
				# Retry once after ensuring attributes
				try:
					variant = create_variant(template_item.name, {"attributes": [{"attribute": k, "attribute_value": v} for k, v in attr_values.items()]})
					try:
						variant.item_code = erp_sku
					except Exception:
						pass
					for field, value in [
						("salla_store", template_item.get("salla_store")),
						("salla_product_id", product_id),
						("salla_sku", erp_sku),
					]:
						if field in variant.as_dict():
							try:
								variant.set(field, value)
							except Exception:
								pass
					variant.save(ignore_permissions=True)
					try:
						if erp_sku and variant.name != erp_sku:
							from frappe.model.rename_doc import rename_doc
							rename_doc("Item", variant.name, erp_sku, force=True, merge=False)
							variant = frappe.get_doc("Item", erp_sku)
					except Exception as e_rename:
						frappe.log_error("Salla Variant Rename", f"Rename to SKU failed for {variant.name} -> {erp_sku}: {e_rename}")
					frappe.db.commit()
					log_sync(sync_log, "CREATED - variant (retry)", product_id, erp_sku, variant.name, f"Attributes: {attr_values}")
				except Exception as e2:
					log_sync(sync_log, "ERROR - variant", product_id, erp_sku, "", f"{e2}")
					frappe.log_error("Salla Variant Creation", f"Failed to create variant (retry) for SKU {erp_sku}: {e2}")
			else:
				log_sync(sync_log, "ERROR - variant", product_id, erp_sku, "", msg)
				frappe.log_error("Salla Variant Creation", f"Failed to create variant for SKU {erp_sku}: {msg}")


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
	# Variants creation intentionally disabled per requirements

