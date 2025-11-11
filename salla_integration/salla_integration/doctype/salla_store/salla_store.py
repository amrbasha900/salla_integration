# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe.model.document import Document
from frappe.utils import now, add_to_date, get_datetime
import requests
import secrets
import urllib.parse

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