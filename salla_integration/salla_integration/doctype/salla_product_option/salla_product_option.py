import frappe
from frappe.model.document import Document


class SallaProductOption(Document):
    def before_save(self):
        # keep option name in title if available
        if self.option_name and not getattr(self, "title", None):
            try:
                self.title = self.option_name
            except Exception:
                pass

