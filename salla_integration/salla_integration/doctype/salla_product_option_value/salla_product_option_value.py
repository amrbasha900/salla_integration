import frappe
from frappe.model.document import Document
import json


class SallaProductOptionValue(Document):
    def get_skus(self):
        try:
            return json.loads(self.sku_list or "[]")
        except Exception:
            return []

