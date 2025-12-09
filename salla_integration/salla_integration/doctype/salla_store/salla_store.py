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
        
        store_info_url = "https://api.salla.dev/admin/v2/store/info"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        try:
            response = requests.get(store_info_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            payload = response.json() or {}
            merchant_data = payload.get("data") or {}

            # Core store details
            merchant_id = merchant_data.get("id")
            self.merchant_id = str(merchant_id) if merchant_id is not None else None
            self.merchant_username = merchant_data.get("username")
            self.merchant_name = merchant_data.get("name")
            self.merchant_entity = merchant_data.get("entity")
            self.merchant_email = merchant_data.get("email")
            self.merchant_mobile = merchant_data.get("mobile")
            self.merchant_phone = merchant_data.get("phone")
            self.merchant_avatar = merchant_data.get("avatar")
            self.merchant_store_location = merchant_data.get("store_location")
            self.merchant_plan = merchant_data.get("plan")
            self.merchant_type = merchant_data.get("type")
            self.merchant_status = merchant_data.get("status")
            self.merchant_verified = 1 if merchant_data.get("verified") else 0
            self.merchant_currency = merchant_data.get("currency")
            self.merchant_domain = merchant_data.get("domain")
            self.merchant_about = merchant_data.get("about") or merchant_data.get("description")
            self.merchant_created_at = merchant_data.get("created_at")
            
            # Licenses block
            licenses = merchant_data.get("licenses") or {}
            self.license_tax_number = licenses.get("tax_number")
            self.license_commercial_number = licenses.get("commercial_number")
            self.license_freelance_number = licenses.get("freelance_number")
            
            # Social block
            social = merchant_data.get("social") or {}
            self.social_website = social.get("website")
            self.social_telegram = social.get("telegram")
            self.social_twitter = social.get("twitter")
            # Some responses use 'facebookb'; fall back if present
            self.social_facebook = social.get("facebook") or social.get("facebookb")
            self.social_maroof = social.get("maroof")
            self.social_youtube = social.get("youtube")
            self.social_snapchat = social.get("snapchat")
            self.social_whatsapp = social.get("whatsapp")
            self.social_appstore_link = social.get("appstore_link")
            self.social_googleplay_link = social.get("googleplay_link")
            
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
            self.set_parent_in_children()
            self.set_name_in_children()

            for idx, row in enumerate(self.get("salla_store_tax"), start=1):
                row.idx = idx

            self.update_child_table("salla_store_tax")
            frappe.db.commit()

        message = _("Fetched {0} taxes from Salla. Added {1}, updated {2}.").format(
            processed, added, updated
        )
        if not processed:
            message = _("No taxes were returned by Salla.")

        frappe.msgprint(message)
        return {"fetched": processed, "added": added, "updated": updated}


@frappe.whitelist()
def fetch_store_taxes(store_name: str):
    """Wrapper to fetch taxes for a given store via RPC."""
    if not store_name:
        frappe.throw(_("Store name is required."))

    doc = frappe.get_doc("Salla Store", store_name)
    return doc.fetch_store_taxes()
