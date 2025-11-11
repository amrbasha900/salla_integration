import frappe
from frappe import _
from salla_integration.utils.salla_client import SallaClient
from salla_integration.salla_integration.doctype.salla_integration_settings.salla_integration_settings import (
	get_settings,
)
import json


def ensure_store_root(store_name: str) -> str:
    """Ensure a root node exists named exactly as the store, return its name."""
    # If a node already named like the store exists, use it
    if frappe.db.exists("Salla Category", store_name):
        return store_name

    # Try to find any existing root for this store
    existing_roots = frappe.get_all(
        "Salla Category",
        filters={
            "salla_store": store_name,
            "parent_salla_category": ["in", ["", None]],
        },
        pluck="name",
        limit=1,
    )

    if existing_roots:
        current_name = existing_roots[0]
        if current_name != store_name:
            try:
                frappe.rename_doc("Salla Category", current_name, store_name, force=True, merge=False)
            except Exception:
                pass
        return store_name

    # Create new root, then rename to match the store name
    root = frappe.get_doc({
        "doctype": "Salla Category",
        "is_group": 1,
        "salla_store": store_name,
    })
    root.insert(ignore_permissions=True)
    frappe.db.commit()
    try:
        frappe.rename_doc("Salla Category", root.name, store_name, force=True, merge=False)
    except Exception:
        return root.name
    return store_name


def upsert_category(store_name: str, cat: dict, parent_name: str) -> str:
	"""Create or update a Salla Category node, returning its name."""
	salla_id = str(cat.get("id"))
	cat_name = cat.get("name", salla_id)
	name_label = f"{salla_id} - {cat_name}"
	status = cat.get("status")

	# Find existing by store + salla_category_id
	existing = frappe.db.get_value(
		"Salla Category",
		{"salla_store": store_name, "salla_category_id": salla_id},
		"name",
	)

	if existing:
		# Update name/parent/status if needed
		doc = frappe.get_doc("Salla Category", existing)
		changed = False
		if doc.parent_salla_category != parent_name:
			doc.parent_salla_category = parent_name
			changed = True
		# Map fields
		if doc.category_name != cat_name:
			doc.category_name = cat_name
			changed = True
		if doc.status != status:
			doc.status = status
			changed = True
		if doc.image != (cat.get("image") or ""):
			doc.image = cat.get("image") or ""
			changed = True
		urls = cat.get("urls", {}) or {}
		if doc.url_customer != (urls.get("customer") or ""):
			doc.url_customer = urls.get("customer") or ""
			changed = True
		if doc.url_admin != (urls.get("admin") or ""):
			doc.url_admin = urls.get("admin") or ""
			changed = True
		if doc.parent_id != str(cat.get("parent_id") or "0"):
			doc.parent_id = str(cat.get("parent_id") or "0")
			changed = True
		if doc.sort_order != int(cat.get("sort_order") or 0):
			doc.sort_order = int(cat.get("sort_order") or 0)
			changed = True
		show_in = cat.get("show_in", {}) or {}
		if int(doc.show_in_app or 0) != int(bool(show_in.get("app"))):
			doc.show_in_app = 1 if show_in.get("app") else 0
			changed = True
		if int(doc.show_in_salla_points or 0) != int(bool(show_in.get("salla_points"))):
			doc.show_in_salla_points = 1 if show_in.get("salla_points") else 0
			changed = True
		if int(doc.has_hidden_products or 0) != int(bool(cat.get("has_hidden_products"))):
			doc.has_hidden_products = 1 if cat.get("has_hidden_products") else 0
			changed = True
		if doc.updated_at != (cat.get("update_at") or None):
			doc.updated_at = cat.get("update_at") or None
			changed = True
		metadata = cat.get("metadata", {}) or {}
		if doc.metadata_title != (metadata.get("title") or ""):
			doc.metadata_title = metadata.get("title") or ""
			changed = True
		if doc.metadata_description != (metadata.get("description") or ""):
			doc.metadata_description = metadata.get("description") or ""
			changed = True
		if doc.metadata_url != (metadata.get("url") or ""):
			doc.metadata_url = metadata.get("url") or ""
			changed = True
		new_sub = json.dumps(cat.get("sub_categories", []), ensure_ascii=False, separators=(",", ":"))
		if (doc.sub_categories_json or "") != new_sub:
			doc.sub_categories_json = new_sub
			changed = True
		if doc.name != name_label:
			# Rename node to reflect current label
			try:
				frappe.rename_doc("Salla Category", doc.name, name_label, force=True, merge=False)
				doc = frappe.get_doc("Salla Category", name_label)
				changed = False  # rename saved
			except Exception:
				pass
		if changed:
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		return doc.name

	# Create new node
	doc = frappe.get_doc({
		"doctype": "Salla Category",
		"parent_salla_category": parent_name,
		"is_group": 1 if (cat.get("sub_categories") or cat.get("has_children")) else 0,
		"salla_store": store_name,
		"salla_category_id": salla_id,
		"category_name": cat_name,
		"status": status,
		"image": cat.get("image") or "",
		"url_customer": (cat.get("urls") or {}).get("customer") or "",
		"url_admin": (cat.get("urls") or {}).get("admin") or "",
		"parent_id": str(cat.get("parent_id") or "0"),
		"sort_order": int(cat.get("sort_order") or 0),
		"show_in_app": 1 if ((cat.get("show_in") or {}).get("app")) else 0,
		"show_in_salla_points": 1 if ((cat.get("show_in") or {}).get("salla_points")) else 0,
		"has_hidden_products": 1 if cat.get("has_hidden_products") else 0,
		"updated_at": cat.get("update_at") or None,
		"metadata_title": (cat.get("metadata") or {}).get("title") or "",
		"metadata_description": (cat.get("metadata") or {}).get("description") or "",
		"metadata_url": (cat.get("metadata") or {}).get("url") or "",
		"sub_categories_json": json.dumps(cat.get("sub_categories", []), ensure_ascii=False, separators=(",", ":")),
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	try:
		frappe.rename_doc("Salla Category", doc.name, name_label, force=True, merge=False)
		return name_label
	except Exception:
		return doc.name


@frappe.whitelist()
def sync_categories(store_name: str):
	"""
	Fetch categories from Salla and build/update the tree under a per-store root.
	Reference: List Categories endpoint (requires categories.read scope).
	Docs: https://docs.salla.dev/5394207e0
	"""
	settings = get_settings()
	client = SallaClient(store_name)
	root_name = ensure_store_root(store_name)

	# Fetch all categories first (generally limited), then attach in parent-first order
	categories = client.get_all_pages("get_categories", per_page=min(settings.batch_size or 60, 60))

	# Index by id
	id_to_cat = {str(c.get("id")): c for c in categories}
	processed = set()
	name_by_id = {}

	def attach(cat_id: str):
		if cat_id in processed:
			return
		cat = id_to_cat.get(cat_id)
		if not cat:
			return
		parent_id = str(cat.get("parent_id") or "0")
		if parent_id == "0":
			parent_name = root_name
		else:
			# Ensure parent exists first
			if parent_id not in processed:
				attach(parent_id)
			parent_name = name_by_id.get(parent_id, root_name)
		# Upsert this category
		node_name = upsert_category(store_name, cat, parent_name)
		name_by_id[cat_id] = node_name
		processed.add(cat_id)

	for cat in categories:
		attach(str(cat.get("id")))

	return {
		"success": True,
		"total": len(categories),
	}


