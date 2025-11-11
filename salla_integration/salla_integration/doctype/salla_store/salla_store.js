// Copyright (c) 2025, Amr Basha and contributors
// For license information, please see license.txt

frappe.ui.form.on('Salla Store', {
    refresh: function(frm) {
        // Show authorization URL if store is not authorized
        if (!frm.doc.is_authorized && frm.doc.client_id && frm.doc.client_secret) {
            frm.trigger('generate_authorization_url');
        }
        
        // Add custom buttons
        if (!frm.doc.is_authorized) {
            frm.add_custom_button(__('Authorize Store'), function() {
                frm.trigger('authorize_store');
            }).addClass('btn-primary');
        } else {
            frm.add_custom_button(__('Revoke Authorization'), function() {
                frm.trigger('revoke_authorization');
            }).addClass('btn-danger');
            
            frm.add_custom_button(__('Test Connection'), function() {
                frm.trigger('test_connection');
            });
            
            frm.add_custom_button(__('Sync Now'), function() {
                frm.trigger('sync_now');
            }).addClass('btn-primary');
        }
    },
    
    client_id: function(frm) {
        if (frm.doc.client_id && frm.doc.client_secret) {
            frm.trigger('generate_authorization_url');
        }
    },
    
    client_secret: function(frm) {
        if (frm.doc.client_id && frm.doc.client_secret) {
            frm.trigger('generate_authorization_url');
        }
    },
    
    generate_authorization_url: function(frm) {
        if (!frm.doc.name || frm.doc.__islocal) {
            frappe.msgprint(__('Please save the document first'));
            return;
        }
        
        frappe.call({
            method: 'get_authorization_url',
            doc: frm.doc,
            callback: function(r) {
                if (r.message) {
                    frm.set_value('authorization_url', r.message);
                }
            }
        });
    },
    
    authorize_store: function(frm) {
        if (!frm.doc.authorization_url) {
            frappe.msgprint(__('Please save the document first to generate authorization URL'));
            return;
        }
        
        // Open Salla authorization page in new window
        const authWindow = window.open(
            frm.doc.authorization_url,
            'SallaAuth',
            'width=600,height=700,scrollbars=yes'
        );
        
        // Check if window opened successfully
        if (!authWindow) {
            frappe.msgprint({
                title: __('Popup Blocked'),
                indicator: 'red',
                message: __('Please allow popups for this site and try again.')
            });
            return;
        }
        
        frappe.msgprint({
            title: __('Authorization Started'),
            indicator: 'blue',
            message: __('Please complete the authorization in the popup window. This page will refresh automatically once authorization is complete.')
        });
        
        // Poll for authorization completion
        const pollInterval = setInterval(function() {
            frappe.call({
                method: 'frappe.client.get_value',
                args: {
                    doctype: 'Salla Store',
                    filters: { name: frm.doc.name },
                    fieldname: 'is_authorized'
                },
                callback: function(r) {
                    if (r.message && r.message.is_authorized) {
                        clearInterval(pollInterval);
                        authWindow.close();
                        frm.reload_doc();
                        frappe.show_alert({
                            message: __('Store authorized successfully!'),
                            indicator: 'green'
                        }, 5);
                    }
                }
            });
        }, 2000);
        
        // Stop polling after 5 minutes
        setTimeout(function() {
            clearInterval(pollInterval);
        }, 300000);
    },
    
    revoke_authorization: function(frm) {
        frappe.confirm(
            __('Are you sure you want to revoke authorization for this store?'),
            function() {
                frappe.call({
                    method: 'revoke_authorization',
                    doc: frm.doc,
                    callback: function(r) {
                        frm.reload_doc();
                    }
                });
            }
        );
    },
    
    test_connection: function(frm) {
        frappe.call({
            method: 'salla_integration.api.test.test_connection',
            args: {
                store_name: frm.doc.name
            },
            callback: function(r) {
                if (r.message && r.message.success) {
                    frappe.msgprint({
                        title: __('Connection Successful'),
                        indicator: 'green',
                        message: __('Connected to: {0}', [r.message.merchant_name || 'Salla Store'])
                    });
                } else {
                    frappe.msgprint({
                        title: __('Connection Failed'),
                        indicator: 'red',
                        message: r.message ? r.message.error : __('Unknown error')
                    });
                }
            }
        });
    },
    
    sync_now: function(frm) {
        frappe.confirm(
            __('Start syncing data from Salla? This may take a few minutes.'),
            function() {
                frappe.call({
                    method: 'salla_integration.api.sync.start_sync',
                    args: {
                        store_name: frm.doc.name
                    },
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.msgprint({
                                title: __('Sync Started'),
                                indicator: 'blue',
                                message: __('Sync job has been queued. Check Sync Logs for progress.')
                            });
                        }
                    }
                });
            }
        );
    }
});