// Copyright (c) 2025, Your Company
// License: MIT

frappe.ui.form.on('Sales Order', {
    refresh: function(frm) {
        // Only show Salla buttons if order is linked to Salla
        if (frm.doc.salla_order_id && frm.doc.salla_store) {
            add_salla_buttons(frm);
            add_salla_indicators(frm);
            show_salla_info(frm);
        }
    },
    
    salla_store: function(frm) {
        // When store is selected, show store info
        if (frm.doc.salla_store) {
            load_store_info(frm);
        }
    },
    
    on_submit: function(frm) {
        // Show notification if order will sync to Salla
        if (frm.doc.salla_order_id && frm.doc.salla_store) {
            check_sync_settings(frm);
        }
    }
});

function add_salla_buttons(frm) {
    // View in Salla button
    frm.add_custom_button(__('View in Salla'), function() {
        view_in_salla(frm);
    }, __('Salla'));
    
    // Update status in Salla button
    if (frm.doc.docstatus === 1) {
        frm.add_custom_button(__('Update Status in Salla'), function() {
            update_status_in_salla(frm);
        }, __('Salla'));
    }
    
    // Refresh from Salla button
    frm.add_custom_button(__('Refresh from Salla'), function() {
        refresh_from_salla(frm);
    }, __('Salla'));
    
    // View Sync Log button
    frm.add_custom_button(__('View Sync Logs'), function() {
        view_sync_logs(frm);
    }, __('Salla'));
}

function add_salla_indicators(frm) {
    // Add indicator based on sync status
    let indicator_map = {
        'Synced': ['green', 'Synced with Salla'],
        'Not Synced': ['orange', 'Not Synced'],
        'Failed': ['red', 'Sync Failed']
    };
    
    let sync_status = frm.doc.salla_sync_status || 'Not Synced';
    let indicator = indicator_map[sync_status] || ['grey', 'Unknown'];
    
    frm.dashboard.add_indicator(indicator[1], indicator[0]);
}

function show_salla_info(frm) {
    // Show Salla order information in a formatted way
    let salla_info_html = `
        <div class="salla-order-info" style="background: #f0f4ff; padding: 15px; border-radius: 8px; margin: 10px 0;">
            <h5 style="margin: 0 0 10px 0; color: #667eea;">
                <i class="fa fa-shopping-cart"></i> Salla Order Information
            </h5>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                <div>
                    <strong>Order ID:</strong> ${frm.doc.salla_order_id}
                </div>
                <div>
                    <strong>Reference ID:</strong> ${frm.doc.salla_reference_id || 'N/A'}
                </div>
                <div>
                    <strong>Store:</strong> ${frm.doc.salla_store}
                </div>
                <div>
                    <strong>Sync Status:</strong> 
                    <span class="badge badge-${get_sync_badge_color(frm.doc.salla_sync_status)}">
                        ${frm.doc.salla_sync_status || 'Not Synced'}
                    </span>
                </div>
            </div>
            ${frm.doc.salla_last_synced ? `
                <div style="margin-top: 10px; font-size: 12px; color: #718096;">
                    <i class="fa fa-clock-o"></i> Last synced: ${frappe.datetime.comment_when(frm.doc.salla_last_synced)}
                </div>
            ` : ''}
        </div>
    `;
    
    frm.dashboard.add_comment(salla_info_html, 'blue', true);
}

function get_sync_badge_color(status) {
    const color_map = {
        'Synced': 'success',
        'Not Synced': 'warning',
        'Failed': 'danger'
    };
    return color_map[status] || 'secondary';
}

function view_in_salla(frm) {
    if (!frm.doc.salla_order_id) {
        frappe.msgprint(__('No Salla Order ID found'));
        return;
    }
    
    // Open Salla order page
    // Note: Adjust URL based on actual Salla order URL structure
    let salla_url = `https://s.salla.sa/orders/${frm.doc.salla_order_id}`;
    window.open(salla_url, '_blank');
}

function update_status_in_salla(frm) {
    let status_options = [
        {label: __('Processing'), value: 'processing'},
        {label: __('Shipped'), value: 'shipped'},
        {label: __('Completed'), value: 'completed'},
        {label: __('Cancelled'), value: 'cancelled'}
    ];
    
    let d = new frappe.ui.Dialog({
        title: __('Update Order Status in Salla'),
        fields: [
            {
                label: __('New Status'),
                fieldname: 'status',
                fieldtype: 'Select',
                options: status_options.map(opt => opt.label),
                reqd: 1,
                description: __('Select the status to update in Salla')
            }
        ],
        primary_action_label: __('Update Status'),
        primary_action: function(values) {
            // Map label to value
            let selected_label = values.status;
            let selected_status = status_options.find(opt => opt.label === selected_label);
            
            if (!selected_status) {
                frappe.msgprint(__('Invalid status selected'));
                return;
            }
            
            frappe.call({
                method: 'salla_integration.api.orders.update_order_status_in_salla',
                args: {
                    sales_order_name: frm.doc.name,
                    status: selected_status.value
                },
                freeze: true,
                freeze_message: __('Updating status in Salla...'),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({
                            message: __('Status updated in Salla successfully'),
                            indicator: 'green'
                        }, 5);
                        frm.reload_doc();
                        d.hide();
                    } else {
                        frappe.msgprint({
                            title: __('Update Failed'),
                            indicator: 'red',
                            message: r.message ? r.message.error : __('Unknown error occurred')
                        });
                    }
                }
            });
        }
    });
    
    d.show();
}

function refresh_from_salla(frm) {
    frappe.confirm(
        __('Fetch latest order details from Salla?'),
        function() {
            frappe.call({
                method: 'salla_integration.api.orders.refresh_order_from_salla',
                args: {
                    sales_order_name: frm.doc.name
                },
                freeze: true,
                freeze_message: __('Fetching from Salla...'),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({
                            message: __('Order refreshed from Salla'),
                            indicator: 'green'
                        }, 5);
                        
                        if (r.message.changes) {
                            frappe.msgprint({
                                title: __('Changes Detected'),
                                message: r.message.changes,
                                indicator: 'blue'
                            });
                        }
                        
                        frm.reload_doc();
                    } else {
                        frappe.msgprint({
                            title: __('Refresh Failed'),
                            indicator: 'red',
                            message: r.message ? r.message.error : __('Unknown error occurred')
                        });
                    }
                }
            });
        }
    );
}

function view_sync_logs(frm) {
    frappe.route_options = {
        "salla_store": frm.doc.salla_store,
        "sync_type": "Orders"
    };
    frappe.set_route("List", "Salla Sync Log");
}

function load_store_info(frm) {
    if (!frm.doc.salla_store) return;
    
    frappe.call({
        method: 'frappe.client.get_value',
        args: {
            doctype: 'Salla Store',
            filters: {name: frm.doc.salla_store},
            fieldname: ['store_name', 'is_authorized']
        },
        callback: function(r) {
            if (r.message) {
                let store_info = r.message;
                
                if (!store_info.is_authorized) {
                    frm.dashboard.set_headline_alert(
                        `<div class="alert alert-warning">
                            <i class="fa fa-warning"></i>
                            Store "${store_info.store_name}" is not authorized. 
                            Status updates will not sync to Salla.
                        </div>`,
                        'yellow'
                    );
                }
            }
        }
    });
}

function check_sync_settings(frm) {
    frappe.call({
        method: 'frappe.client.get_value',
        args: {
            doctype: 'Salla Integration Settings',
            filters: {name: 'Salla Integration Settings'},
            fieldname: ['update_order_status_to_salla']
        },
        callback: function(r) {
            if (r.message && r.message.update_order_status_to_salla) {
                frappe.show_alert({
                    message: __('Order status will be updated in Salla'),
                    indicator: 'blue'
                }, 5);
            }
        }
    });
}

// Add custom styling
frappe.ui.form.on('Sales Order', {
    onload: function(frm) {
        // Add custom CSS for Salla section
        if (!$('style#salla-order-style').length) {
            $('head').append(`
                <style id="salla-order-style">
                    .salla-order-info {
                        animation: slideIn 0.3s ease-out;
                    }
                    @keyframes slideIn {
                        from {
                            opacity: 0;
                            transform: translateY(-10px);
                        }
                        to {
                            opacity: 1;
                            transform: translateY(0);
                        }
                    }
                    .frappe-control[data-fieldname="salla_integration_section"] {
                        background: #f8fafc;
                        padding: 15px;
                        border-radius: 8px;
                        border: 1px solid #e2e8f0;
                    }
                </style>
            `);
        }
    }
});

// List view customization
frappe.listview_settings['Sales Order'] = {
    ...frappe.listview_settings['Sales Order'],
    
    get_indicator: function(doc) {
        // Show indicator if order is from Salla
        if (doc.salla_order_id) {
            if (doc.salla_sync_status === 'Synced') {
                return [__("Salla Order - Synced"), "green", "salla_order_id,!=,''"];
            } else if (doc.salla_sync_status === 'Failed') {
                return [__("Salla Order - Sync Failed"), "red", "salla_order_id,!=,''"];
            } else {
                return [__("Salla Order - Not Synced"), "orange", "salla_order_id,!=,''"];
            }
        }
        
        // Fall back to default indicator
        const original_get_indicator = frappe.listview_settings['Sales Order'].__proto__.get_indicator;
        if (original_get_indicator) {
            return original_get_indicator(doc);
        }
    },
    
    formatters: {
        salla_store: function(value) {
            if (value) {
                return `<span class="badge badge-info">${value}</span>`;
            }
            return '';
        },
        salla_sync_status: function(value) {
            const badge_map = {
                'Synced': 'success',
                'Not Synced': 'warning',
                'Failed': 'danger'
            };
            const badge_class = badge_map[value] || 'secondary';
            return value ? `<span class="badge badge-${badge_class}">${value}</span>` : '';
        }
    },
    
    onload: function(listview) {
        // Add custom filter buttons
        listview.page.add_inner_button(__('Salla Orders'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([['Sales Order', 'salla_order_id', '!=', '']]);
        });
        
        listview.page.add_inner_button(__('Failed Sync'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([
                ['Sales Order', 'salla_order_id', '!=', ''],
                ['Sales Order', 'salla_sync_status', '=', 'Failed']
            ]);
        });
        
        // Add bulk action for syncing to Salla
        listview.page.add_actions_menu_item(__('Sync to Salla'), function() {
            let selected_docs = listview.get_checked_items();
            
            if (selected_docs.length === 0) {
                frappe.msgprint(__('Please select orders to sync'));
                return;
            }
            
            frappe.confirm(
                __('Sync {0} selected orders to Salla?', [selected_docs.length]),
                function() {
                    frappe.call({
                        method: 'salla_integration.api.orders.bulk_sync_orders',
                        args: {
                            sales_order_names: selected_docs.map(doc => doc.name)
                        },
                        freeze: true,
                        freeze_message: __('Syncing orders...'),
                        callback: function(r) {
                            if (r.message) {
                                frappe.msgprint({
                                    title: __('Sync Completed'),
                                    message: __('Successfully synced {0} out of {1} orders', 
                                               [r.message.success, selected_docs.length]),
                                    indicator: 'green'
                                });
                                listview.refresh();
                            }
                        }
                    });
                }
            );
        }, true);
    }
};