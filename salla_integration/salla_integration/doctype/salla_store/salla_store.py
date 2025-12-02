# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now, add_to_date, get_datetime, flt
import requests
import secrets
import urllib.parse
from salla_integration.utils.salla_client import SallaClient

class SallaStore(Document):
    def validate(self):
        """Validate store configuration"""
        if not self.redirect_uri:
            # Auto-generate redirect URI based on site
            site_url = frappe.utils.get_url()
            # Remove trailing slash if exists
            site_url = site_url.rstrip('/')
            self.redirect_uri = f"{site_url}/api/method/salla_integration.api.auth.callback"
        
        # Validate redirect URI format
        if self.redirect_uri:
            # Warn if using http instead of https
            if self.redirect_uri.startswith('http://') and not frappe.conf.get('developer_mode'):
                frappe.msgprint(
                    "Warning: Using HTTP for redirect URI. Salla requires HTTPS in production.",
                    indicator='orange',
                    alert=True
                )
    
    def before_insert(self):
        """Generate state token for OAuth security"""
        if not self.state_token:
            self.state_token = secrets.token_urlsafe(32)
    
    @frappe.whitelist()
    def get_authorization_url(self):
        """Generate Salla OAuth authorization URL"""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": "offline_access",  # Request refresh token
            "state": self.state_token
        }
        
        base_url = "https://accounts.salla.sa/oauth2/auth"
        # URL-encode query parameters to ensure exact matching and proper formatting
        query_string = urllib.parse.urlencode(params)
        
        return f"{base_url}?{query_string}"
    
    def exchange_code_for_token(self, code):
        """Exchange authorization code for access token"""
        token_url = "https://accounts.salla.sa/oauth2/token"
        
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.get_password("client_secret"),
            "code": code,
            "redirect_uri": self.redirect_uri
        }
        
        try:
            response = requests.post(token_url, data=payload, timeout=30)
            response.raise_for_status()
            
            token_data = response.json()
            
            # Guard: ensure access_token is present
            if not token_data.get("access_token"):
                frappe.log_error(
                    f"Token exchange response missing access_token. Raw: {response.text}",
                    "Salla Authorization"
                )
                frappe.throw("Failed to authorize store: access_token not returned by Salla")
            
            # Store tokens
            self.access_token = token_data.get("access_token")
            self.refresh_token = token_data.get("refresh_token")
            
            # Calculate expiry (expires_in is in seconds)
            expires_in = token_data.get("expires_in", 1209600)  # Default 14 days
            self.token_expires_at = add_to_date(now(), seconds=expires_in)
            
            self.is_authorized = 1
            self.last_sync_datetime = now()
            
            # Fetch merchant info
            self.fetch_merchant_info()
            
            self.save(ignore_permissions=True)
            frappe.db.commit()
            
            frappe.msgprint(f"Successfully authorized store: {self.store_name}", alert=True)
            
            return True
            
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"Salla OAuth Error: {str(e)}", "Salla Authorization Failed")
            frappe.throw(f"Failed to authorize store: {str(e)}")
    
    def fetch_merchant_info(self):
        """Fetch merchant details from Salla"""
        if not self.access_token:
            return
        
        user_info_url = "https://accounts.salla.sa/oauth2/user/info"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        try:
            response = requests.get(user_info_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            merchant_data = response.json()
            
            self.merchant_id = merchant_data.get("id")
            self.merchant_email = merchant_data.get("email")
            self.merchant_name = merchant_data.get("name")
            
        except Exception as e:
            frappe.log_error(f"Failed to fetch merchant info: {str(e)}", "Salla Merchant Info")
    
    def is_token_expired(self):
        """Check if access token is expired"""
        if not self.token_expires_at:
            return True
        
        return get_datetime(self.token_expires_at) <= get_datetime(now())
    
    def refresh_access_token(self):
        """Refresh expired access token"""
        if not self.refresh_token:
            frappe.throw("No refresh token available. Please re-authorize the store.")
        
        token_url = "https://accounts.salla.sa/oauth2/token"
        
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.get_password("client_secret"),
            "refresh_token": self.refresh_token
        }
        
        try:
            response = requests.post(token_url, data=payload, timeout=30)
            response.raise_for_status()
            
            token_data = response.json()
            
            self.access_token = token_data.get("access_token")
            
            # Update new refresh token if provided
            new_refresh_token = token_data.get("refresh_token")
            if new_refresh_token:
                self.refresh_token = new_refresh_token
            
            expires_in = token_data.get("expires_in", 1209600)
            self.token_expires_at = add_to_date(now(), seconds=expires_in)
            
            self.save(ignore_permissions=True)
            
            frappe.db.commit()
            
        except Exception as e:
            frappe.log_error(f"Token Refresh Error: {str(e)}", "Salla Token Refresh Failed")
            frappe.throw(f"Failed to refresh token: {str(e)}")
    
    def get_valid_token(self):
        """Get valid access token, refresh if needed"""
        if self.is_token_expired():
            self.refresh_access_token()
        
        return self.access_token
    
    @frappe.whitelist()
    def revoke_authorization(self):
        """Revoke store authorization"""
        self.is_authorized = 0
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.save(ignore_permissions=True)
        
        frappe.msgprint(f"Authorization revoked for: {self.store_name}", alert=True)

    @frappe.whitelist()
    def fetch_store_taxes(self):
        """Pull tax configurations from Salla and update the child table."""
        if self.is_new():
            frappe.throw(_("Please save the store before fetching taxes."))

        if not self.is_authorized:
            frappe.throw(_("Please authorize the store before fetching taxes."))

        client = SallaClient(self.name)
        taxes = client.get_all_pages("get_taxes", per_page=client.MAX_PER_PAGE) or []

        existing = {
            row.tax_id: row
            for row in (self.get("salla_store_tax") or [])
            if row.tax_id
        }

        added = 0
        updated = 0
        processed = 0

        for tax_data in taxes:
            tax_id = tax_data.get("id")
            if tax_id is None:
                continue

            processed += 1
            tax_id = str(tax_id)
            status = (tax_data.get("status") or "").lower()
            if status not in ("active", "inactive"):
                status = "inactive"

            tax_rate = flt(tax_data.get("tax"))
            country = tax_data.get("country")

            row = existing.get(tax_id)
            if row:
                changed = False

                if (row.status or "").lower() != status:
                    row.status = status
                    changed = True

                if flt(row.tax) != tax_rate:
                    row.tax = tax_rate
                    changed = True

                if (row.country or "") != (country or ""):
                    row.country = country
                    changed = True

                if changed:
                    updated += 1
            else:
                self.append(
                    "salla_store_tax",
                    {
                        "tax_id": tax_id,
                        "status": status,
                        "tax": tax_rate,
                        "country": country,
                    },
                )
                added += 1

        if added or updated:
            self.save(ignore_permissions=True)
            frappe.db.commit()

        message = _("Fetched {0} taxes from Salla. Added {1}, updated {2}.").format(
            processed, added, updated
        )
        if not processed:
            message = _("No taxes were returned by Salla.")

        frappe.msgprint(message)
        return {"fetched": processed, "added": added, "updated": updated}

    @frappe.whitelist()
    def migrate_salla_taxes(self):
        """Create or update Sales Taxes and Charges Templates based on mapped taxes."""
        if self.is_new():
            frappe.throw(_("Please save the store before migrating taxes."))

        if not self.company:
            frappe.throw(_("Please set the Company before migrating taxes."))

        rows = self.get("salla_store_tax") or []
        if not rows:
            frappe.throw(_("Please fetch taxes from Salla first."))

        created = 0
        updated = 0
        missing_account = 0
        invalid_company = 0
        missing_tax_id = 0

        for row in rows:
            if not row.tax_account:
                missing_account += 1
                continue

            account_company = frappe.db.get_value("Account", row.tax_account, "company")
            if account_company and account_company != self.company:
                invalid_company += 1
                continue

            tax_id = (row.tax_id or "").strip()
            if not tax_id:
                missing_tax_id += 1
                continue

            tax_rate = flt(row.tax)
            template_title = self._build_tax_template_title(row)
            template_filters = {"title": template_title, "company": self.company}
            existing_template_name = frappe.db.get_value(
                "Sales Taxes and Charges Template",
                {"company": self.company, "salla_tax_id": tax_id},
                "name",
            )
            if not existing_template_name:
                existing_template_name = frappe.db.exists(
                    "Sales Taxes and Charges Template", template_filters
                )

            disabled = 0 if (row.status or "").lower() == "active" else 1
            tax_detail = {
                "charge_type": "On Net Total",
                "account_head": row.tax_account,
                "rate": tax_rate,
                "description": self._build_tax_description(row),
            }

            if existing_template_name:
                template = frappe.get_doc(
                    "Sales Taxes and Charges Template", existing_template_name
                )
                detail_row = next(
                    (d for d in template.taxes if d.account_head == row.tax_account),
                    None,
                )
                changed = False

                if detail_row:
                    if flt(detail_row.rate) != tax_rate:
                        detail_row.rate = tax_rate
                        changed = True

                    if detail_row.charge_type != "On Net Total":
                        detail_row.charge_type = "On Net Total"
                        changed = True

                    if (detail_row.description or "").strip() != tax_detail["description"]:
                        detail_row.description = tax_detail["description"]
                        changed = True
                else:
                    template.append("taxes", tax_detail)
                    changed = True

                if (template.salla_tax_id or "").strip() != tax_id:
                    template.salla_tax_id = tax_id
                    changed = True

                if template.disabled != disabled:
                    template.disabled = disabled
                    changed = True

                if changed:
                    template.save(ignore_permissions=True)
                    updated += 1
            else:
                template = frappe.get_doc(
                    {
                        "doctype": "Sales Taxes and Charges Template",
                        "title": template_title,
                        "company": self.company,
                        "salla_tax_id": tax_id,
                        "disabled": disabled,
                        "taxes": [tax_detail],
                    }
                )
                template.insert(ignore_permissions=True)
                created += 1

        if created or updated:
            frappe.db.commit()

        message = _("Migration complete. Created: {0}, updated: {1}, skipped (no tax account): {2}, skipped (missing Salla tax ID): {3}.").format(
            created, updated, missing_account, missing_tax_id
        )
        if invalid_company:
            message += " " + _(
                "Skipped {0} rows because the selected Tax Account belongs to another company."
            ).format(invalid_company)

        frappe.msgprint(message)
        return {
            "created": created,
            "updated": updated,
            "skipped_without_account": missing_account,
            "skipped_invalid_company": invalid_company,
            "skipped_without_tax_id": missing_tax_id,
        }

    def _build_tax_template_title(self, tax_row):
        """Build a deterministic Sales Tax Template title for a Salla tax row."""
        store_label = self.store_name or self.name
        country = tax_row.country or _("General")
        tax_rate = flt(tax_row.tax)
        tax_id = tax_row.tax_id or _("Unknown")
        return f"{store_label} - {country} - {tax_rate:.2f}% (Salla Tax {tax_id})"

    def _build_tax_description(self, tax_row):
        """Return a user-friendly description for the Sales Tax row."""
        country = tax_row.country or _("General")
        status = (tax_row.status or _("unknown")).title()
        tax_id = tax_row.tax_id or _("Unknown")
        return f"{country} - {status} (Salla Tax {tax_id})"


@frappe.whitelist()
def fetch_store_taxes(store_name: str):
    """Wrapper to fetch taxes for a given store via RPC."""
    if not store_name:
        frappe.throw(_("Store name is required."))

    doc = frappe.get_doc("Salla Store", store_name)
    return doc.fetch_store_taxes()


@frappe.whitelist()
def migrate_salla_taxes(store_name: str):
    """Wrapper to migrate taxes for a given store via RPC."""
    if not store_name:
        frappe.throw(_("Store name is required."))

    doc = frappe.get_doc("Salla Store", store_name)
    return doc.migrate_salla_taxes()