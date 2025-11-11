# Copyright (c) 2025, Your Company
# License: MIT

import frappe
from frappe import _

@frappe.whitelist(allow_guest=True)
def callback():
    """
    OAuth callback endpoint that Salla redirects to after authorization.
    URL: /api/method/salla_integration.api.auth.callback
    """
    # Get parameters from callback URL
    code = frappe.form_dict.get("code")
    state = frappe.form_dict.get("state")
    error = frappe.form_dict.get("error")
    
    # Handle authorization errors
    if error:
        error_description = frappe.form_dict.get("error_description", "Unknown error")
        return build_error_response(f"Authorization failed: {error_description}")
    
    # Validate required parameters
    if not code or not state:
        return build_error_response("Missing authorization code or state parameter")
    
    # Find the store by state token
    try:
        store_name = frappe.db.get_value("Salla Store", {"state_token": state}, "name")
        
        if not store_name:
            return build_error_response("Invalid state token. Store not found.")
        
        # Get store document
        store = frappe.get_doc("Salla Store", store_name)
        
        # Exchange code for access token
        store.exchange_code_for_token(code)
        
        # Return success page
        return build_success_response(store.store_name)
        
    except Exception as e:
        frappe.log_error(f"OAuth Callback Error: {str(e)}", "Salla OAuth Callback")
        return build_error_response(str(e))


def build_success_response(store_name):
    """Build HTML success response"""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Authorization Successful</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
            }}
            .container {{
                background: white;
                padding: 40px;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 500px;
                width: 100%;
            }}
            .success-icon {{
                width: 80px;
                height: 80px;
                background: #10b981;
                border-radius: 50%;
                margin: 0 auto 20px;
                display: flex;
                align-items: center;
                justify-content: center;
                animation: scaleIn 0.5s ease-out;
            }}
            .success-icon svg {{
                width: 50px;
                height: 50px;
                fill: white;
            }}
            h1 {{
                color: #1a202c;
                margin: 0 0 10px;
                font-size: 28px;
                animation: fadeIn 0.6s ease-out;
            }}
            p {{
                color: #4a5568;
                font-size: 16px;
                line-height: 1.6;
                margin: 10px 0;
            }}
            .store-name {{
                background: #f7fafc;
                padding: 10px 20px;
                border-radius: 6px;
                font-weight: 600;
                color: #2d3748;
                margin: 20px 0;
                font-size: 18px;
            }}
            .close-btn {{
                background: #667eea;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 6px;
                font-size: 16px;
                cursor: pointer;
                margin-top: 20px;
                transition: background 0.3s ease;
            }}
            .close-btn:hover {{
                background: #5568d3;
            }}
            .info {{
                font-size: 14px;
                color: #718096;
                margin-top: 15px;
            }}
            @keyframes scaleIn {{
                from {{
                    transform: scale(0);
                    opacity: 0;
                }}
                to {{
                    transform: scale(1);
                    opacity: 1;
                }}
            }}
            @keyframes fadeIn {{
                from {{
                    opacity: 0;
                    transform: translateY(-10px);
                }}
                to {{
                    opacity: 1;
                    transform: translateY(0);
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                </svg>
            </div>
            <h1>Authorization Successful!</h1>
            <p>Your Salla store has been successfully connected to ERPNext.</p>
            <div class="store-name">{store_name}</div>
            <p>You can now close this window and return to ERPNext.</p>
            <p class="info">The main window will refresh automatically.</p>
            <button class="close-btn" onclick="window.close()">Close Window</button>
        </div>
        <script>
            // Auto-close after 5 seconds
            setTimeout(function() {{
                window.close();
            }}, 5000);
            
            // Try to close immediately (some browsers allow this)
            if (window.opener) {{
                window.opener.postMessage({{type: 'salla_auth_success'}}, '*');
            }}
        </script>
    </body>
    </html>
    """
    
    # Return inline HTML response for browser rendering
    frappe.response["type"] = "download"
    frappe.response["display_content_as"] = "inline"
    frappe.response["content_type"] = "text/html"
    frappe.response["filename"] = "salla_auth.html"
    frappe.response["filecontent"] = html
    return


def build_error_response(error_message):
    """Build HTML error response"""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Authorization Failed</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                padding: 20px;
            }}
            .container {{
                background: white;
                padding: 40px;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 500px;
                width: 100%;
            }}
            .error-icon {{
                width: 80px;
                height: 80px;
                background: #ef4444;
                border-radius: 50%;
                margin: 0 auto 20px;
                display: flex;
                align-items: center;
                justify-content: center;
                animation: shake 0.5s ease-out;
            }}
            .error-icon svg {{
                width: 50px;
                height: 50px;
                fill: white;
            }}
            h1 {{
                color: #1a202c;
                margin: 0 0 10px;
                font-size: 28px;
            }}
            p {{
                color: #4a5568;
                font-size: 16px;
                line-height: 1.6;
                margin: 10px 0;
            }}
            .error-message {{
                background: #fef2f2;
                padding: 15px;
                border-radius: 6px;
                color: #991b1b;
                margin: 20px 0;
                border-left: 4px solid #ef4444;
                text-align: left;
                font-family: monospace;
                font-size: 14px;
                word-break: break-word;
            }}
            .close-btn {{
                background: #ef4444;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 6px;
                font-size: 16px;
                cursor: pointer;
                margin-top: 20px;
                transition: background 0.3s ease;
            }}
            .close-btn:hover {{
                background: #dc2626;
            }}
            .help-text {{
                font-size: 14px;
                color: #718096;
                margin-top: 15px;
            }}
            @keyframes shake {{
                0%, 100% {{ transform: translateX(0); }}
                25% {{ transform: translateX(-10px); }}
                75% {{ transform: translateX(10px); }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="error-icon">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                </svg>
            </div>
            <h1>Authorization Failed</h1>
            <p>There was a problem connecting your Salla store.</p>
            <div class="error-message">{error_message}</div>
            <p>Please close this window and try again.</p>
            <p class="help-text">If the problem persists, check your Client ID and Client Secret.</p>
            <button class="close-btn" onclick="window.close()">Close Window</button>
        </div>
        <script>
            // Notify parent window of error
            if (window.opener) {{
                window.opener.postMessage({{type: 'salla_auth_error', error: '{error_message}'}}, '*');
            }}
        </script>
    </body>
    </html>
    """
    
    # Return inline HTML response for browser rendering
    frappe.response["type"] = "download"
    frappe.response["display_content_as"] = "inline"
    frappe.response["content_type"] = "text/html"
    frappe.response["filename"] = "salla_auth.html"
    frappe.response["filecontent"] = html
    return