# Copyright (c) 2025, Your Company
# License: MIT

import frappe
import requests
from typing import Optional, Dict, Any
import time

class SallaClient:
    """Salla API Client for making authenticated requests"""
    
    BASE_URL = "https://api.salla.dev/admin/v2"
    MAX_PER_PAGE = 60  # per docs
    
    def __init__(self, store_name: str):
        """
        Initialize Salla client with store credentials
        
        Args:
            store_name: Name of the Salla Store DocType
        """
        self.store = frappe.get_doc("Salla Store", store_name)
        
        if not self.store.is_authorized:
            frappe.throw(f"Store {store_name} is not authorized. Please authorize first.")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers with valid token"""
        token = self.store.get_valid_token()
        
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
    
    def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        retry: int = 3
    ) -> Dict[str, Any]:
        """
        Make HTTP request to Salla API with error handling and retry logic
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (e.g., '/products')
            params: Query parameters
            data: Request body data
            retry: Number of retry attempts
            
        Returns:
            Response JSON data
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers()
        
        for attempt in range(retry):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=data,
                    timeout=30
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    frappe.log_error("Salla Rate Limit", f"Rate limited. Waiting {retry_after}s")
                    
                    if attempt < retry - 1:
                        time.sleep(retry_after)
                        continue
                
                response.raise_for_status()

                # Parse response JSON
                resp_json = response.json()

                # Rate limit headers handling
                try:
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    reset_after = response.headers.get("X-RateLimit-Reset")
                    if remaining is not None and str(remaining).isdigit() and int(remaining) <= 0:
                        # Sleep until reset if provided, otherwise small cooldown
                        sleep_secs = int(reset_after) if reset_after and str(reset_after).isdigit() else 1
                        time.sleep(max(0, sleep_secs))
                except Exception:
                    pass

                return resp_json
                
            except requests.exceptions.HTTPError as e:
                error_msg = f"HTTP Error: {e.response.status_code}"
                
                try:
                    error_data = e.response.json()
                    error_msg += f" - {error_data.get('message', str(e))}"
                except:
                    error_msg += f" - {str(e)}"
                
                frappe.log_error(f"Salla API Error - {endpoint}", error_msg)
                
                if attempt == retry - 1:
                    frappe.throw(error_msg)
                
                time.sleep(2 ** attempt)  # Exponential backoff
                
            except requests.exceptions.RequestException as e:
                frappe.log_error(f"Salla API Error - {endpoint}", f"Request Error: {str(e)}")
                
                if attempt == retry - 1:
                    frappe.throw(f"Failed to connect to Salla API: {str(e)}")
                
                time.sleep(2 ** attempt)
    
    # ==================== Product Methods ====================
    
    def get_products(self, page: int = 1, per_page: int = 60) -> Dict[str, Any]:
        """
        Get products from Salla
        
        Args:
            page: Page number
            per_page: Items per page (max 50)
            
        Returns:
            Products data with pagination info
        """
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        
        return self._make_request("GET", "/products", params=params)
    
    def get_product(self, product_id: str) -> Dict[str, Any]:
        """Get single product by ID"""
        return self._make_request("GET", f"/products/{product_id}")
    
    def get_product_variants(self, product: str, page: int = 1, per_page: int = 60) -> Dict[str, Any]:
        """
        List product variants for a given product ID
        """
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        return self._make_request("GET", f"/products/{product}/variants", params=params)
    
    def update_product(self, product_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update product in Salla"""
        return self._make_request("PUT", f"/products/{product_id}", data=data)
    
    def update_product_quantity(self, product_id: str, quantity: int) -> Dict[str, Any]:
        """Update product inventory quantity"""
        data = {"quantity": quantity}
        return self._make_request("PUT", f"/products/{product_id}/quantity", data=data)
    
    # ==================== Order Methods ====================
    
    def get_orders(self, page: int = 1, per_page: int = 60, status: Optional[str] = None) -> Dict[str, Any]:
        """
        Get orders from Salla
        
        Args:
            page: Page number
            per_page: Items per page
            status: Filter by order status (e.g., 'pending', 'processing', 'completed')
            
        Returns:
            Orders data with pagination
        """
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        
        if status:
            params["status"] = status
        
        return self._make_request("GET", "/orders", params=params)
    
    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Get single order by ID"""
        return self._make_request("GET", f"/orders/{order_id}")
    
    def get_order_items(self, order_id: str) -> Dict[str, Any]:
        """
        List items for a specific order
        """
        params = {"order_id": order_id}
        return self._make_request("GET", "/orders/items", params=params)
    
    def update_order_status(self, order_id: str, status: str) -> Dict[str, Any]:
        """
        Update order status
        
        Args:
            order_id: Salla order ID
            status: New status (e.g., 'processing', 'shipped', 'completed')
        """
        data = {"status": status}
        return self._make_request("PUT", f"/orders/{order_id}/status", data=data)
    
    def get_order_statuses(self) -> Dict[str, Any]:
        """
        List all order statuses configured in the Salla store.
        """
        return self._make_request("GET", "/orders/statuses")
    
    # ==================== Customer Methods ====================
    
    def get_customers(self, page: int = 1, per_page: int = 60) -> Dict[str, Any]:
        """Get customers from Salla"""
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        
        return self._make_request("GET", "/customers", params=params)
    
    def get_customer(self, customer_id: str) -> Dict[str, Any]:
        """Get single customer by ID"""
        return self._make_request("GET", f"/customers/{customer_id}")

    def get_customer_groups(self, page: int = 1, per_page: int = 60) -> Dict[str, Any]:
        """List customer groups"""
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        return self._make_request("GET", "/customers/groups", params=params)
    
    # ==================== Category Methods ====================
    
    def get_categories(self, page: int = 1, per_page: int = 60) -> Dict[str, Any]:
        """Get product categories"""
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        
        return self._make_request("GET", "/categories", params=params)
    
    # ==================== Brand Methods ====================
    
    def get_brands(self, page: int = 1, per_page: int = 60, include_translations: bool = False) -> Dict[str, Any]:
        """Get product brands"""
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        if include_translations:
            params["with"] = "translations"
        
        return self._make_request("GET", "/brands", params=params)
    
    def get_brand(self, brand_id: str) -> Dict[str, Any]:
        """Get single brand by ID"""
        return self._make_request("GET", f"/brands/{brand_id}")
    
    # ==================== Tax Methods ====================
    
    def get_taxes(self, page: int = 1, per_page: int = 60) -> Dict[str, Any]:
        """
        List store taxes configured in Salla.
        """
        params = {
            "page": page,
            "per_page": min(per_page, self.MAX_PER_PAGE)
        }
        return self._make_request("GET", "/taxes", params=params)
    
    # ==================== Utility Methods ====================
    
    def test_connection(self) -> Dict[str, Any]:
        """Test API connection by fetching merchant info"""
        try:
            # Try to fetch a simple endpoint
            response = self.get_products(page=1, per_page=1)
            
            return {
                "success": True,
                "merchant_name": self.store.merchant_name,
                "store_name": self.store.store_name
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_all_pages(self, method_name: str, **kwargs) -> list:
        """
        Fetch all pages of a paginated endpoint
        
        Args:
            method_name: Name of the client method (e.g., 'get_products')
            **kwargs: Additional arguments for the method
            
        Returns:
            List of all items from all pages
        """
        all_items = []
        page = 1
        per_page = int(kwargs.get("per_page", self.MAX_PER_PAGE))
        
        while True:
            method = getattr(self, method_name)
            response = method(page=page, **kwargs)
            
            # Support either dict with {data, pagination} or a raw list
            if isinstance(response, list):
                data = response
                pagination = None
            else:
                data = response.get("data") if isinstance(response, dict) else []
                if data is None and isinstance(response, dict):
                    # Some endpoints may return array directly without 'data'
                    # Use common alternate key or fallback
                    data = response.get("items", [])
                pagination = response.get("pagination", {}) if isinstance(response, dict) else None
            
            if not data:
                break
            
            all_items.extend(data)
            
            # Check if there are more pages
            if pagination:
                if page >= int(pagination.get("totalPages", 1)):
                    break
            else:
                # Fallback: stop when returned fewer than requested
                if len(data) < per_page:
                    break
            
            page += 1
            
            # Safety limit to prevent infinite loops
            if page > 1000:
                frappe.log_error("Exceeded 1000 pages limit", "Salla Pagination")
                break
        
        return all_items
    def iterate_pages(self, method_name: str, **kwargs):
        """
        Yield items page by page to minimize memory usage.
        
        Args:
            method_name: Name of the client method (e.g., 'get_products')
            **kwargs: Additional arguments for the method
        """
        page = 1
        per_page = int(kwargs.get("per_page", self.MAX_PER_PAGE))
        method = getattr(self, method_name)
        while True:
            response = method(page=page, **kwargs)
            if isinstance(response, list):
                data = response
                pagination = None
            else:
                data = response.get("data") if isinstance(response, dict) else []
                if data is None and isinstance(response, dict):
                    data = response.get("items", [])
                pagination = response.get("pagination", {}) if isinstance(response, dict) else None
            if not data:
                break
            for item in data:
                yield item
            # Next page decision
            if pagination:
                if page >= int(pagination.get("totalPages", 1)):
                    break
            else:
                if len(data) < per_page:
                    break
            page += 1