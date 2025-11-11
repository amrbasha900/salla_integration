// Copyright (c) 2025, Your Company
// License: MIT

frappe.listview_settings['Sales Order'] = {
    add_fields: ["salla_order_id", "salla_store", "salla_sync_status", "salla_last_synced"],
    
    get_indicator: function(doc) {
        // Priority: Show Salla status if order is from Salla
        if (doc.salla_order_id) {
            if (doc.salla_sync_status === 'Synced') {
                return [__("Salla - Synced"), "green", "salla_order_id,!=,''"];
            } else if (doc.salla_sync_status === 'Failed') {
                return [__("Salla - Sync Failed"), "red", "salla_order_id,!=,''"];
            } else {
                return [__("Salla - Not Synced"), "orange", "salla_order_id,!=,''"];
            }
        }
        
        // Fall back to default Sales Order indicators
        if (doc.status === "Draft") {
            return [__("Draft"), "red", "status,=,Draft"];
        } else if (doc.status === "On Hold") {
            return [__("On Hold"), "orange", "status,=,On Hold"];
        } else if (doc.status === "To Deliver and Bill") {
            return [__("To Deliver and Bill"), "orange", "status,=,To Deliver and Bill"];
        } else if (doc.status === "To Bill") {
            return [__("To Bill"), "orange", "status,=,To Bill"];
        } else if (doc.status === "To Deliver") {
            return [__("To Deliver"), "orange", "status,=,To Deliver"];
        } else if (doc.status === "Completed") {
            return [__("Completed"), "green", "status,=,Completed"];
        } else if (doc.status === "Cancelled") {
            return [__("Cancelled"), "red", "status,=,Cancelled"];
        } else if (doc.status === "Closed") {
            return [__("Closed"), "gray", "status,=,Closed"];
        }
    },
    
    formatters: {
        salla_store: function(value) {
            if (value) {
                return `<span class="badge badge-info" style="font-size: 11px;">${value}</span>`;
            }
            return '';
        },
        salla_order_id: function(value) {
            if (value) {
                return `<span class="text-muted" style="font-size: 11px;">
                    <i class="fa fa-shopping-cart"></i> ${value}
                </span>`;
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
            return value ? `<span class="badge badge-${badge_class}" style="font-size: 11px;">${value}</span>` : '';
        }
    },
    
    onload: function(listview) {
        // Add custom filter buttons
        listview.page.add_inner_button(__('Salla Orders'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([['Sales Order', 'salla_order_id', '!=', '']]);
        }).addClass('btn-default');
        
        listview.page.add_inner_button(__('Sync Failed'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([
                ['Sales Order', 'salla_order_id', '!=', ''],
                ['Sales Order', 'salla_sync_status', '=', 'Failed']
            ]);
        }).addClass('btn-default');
        
        listview.page.add_inner_button(__('Not Synced to Salla'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([
                ['Sales Order', 'salla_order_id', '!=', ''],
                ['Sales Order', 'salla_sync_status', '!=', 'Synced']
            ]);
        }).addClass('btn-default');
        
        listview.page.add_inner_button(__('Today\'s Salla Orders'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([
                ['Sales Order', 'salla_order_id', '!=', ''],
                ['Sales Order', 'transaction_date', '=', frappe.datetime.get_today()]
            ]);
        }).addClass('btn-default');
        
        // Add bulk actions for Salla
        listview.page.add_actions_menu_item(__('Sync to Salla (Bulk)'), function() {
            bulk_sync_to_salla(listview);
        }, true);
        
        listview.page.add_actions_menu_item(__('Update Status in Salla'), function() {
            bulk_update_status_in_salla(listview);
        }, true);
        
        listview.page.add_actions_menu_item(__('Refresh from Salla'), function() {
            bulk_refresh_from_salla(listview);
        }, true);
        
        // Add stats
        add_salla_stats(listview);
    },
    
    // Custom buttons for each row
    button: {
        show: function(doc) {
            return doc.salla_order_id;
        },
        get_label: function() {
            return __('Salla');
        },
        get_description: function(doc) {
            return __('View in Salla');
        },
        action: function(doc) {
            window.open(`https://s.salla.sa/orders/${doc.salla_order_id}`, '_blank');
        }
    }
};

function bulk_sync_to_salla(listview) {
    let selected_docs = listview.get_checked_items();
    
    if (selected_docs.length === 0) {
        frappe.msgprint(__('Please select orders to sync'));
        return;
    }
    
    // Filter only Salla orders
    let salla_orders = selected_docs.filter(doc => doc.salla_order_id);
    
    if (salla_orders.length === 0) {
        frappe.msgprint(__('None of the selected orders are from Salla'));
        return;
    }
    
    frappe.confirm(
        __('Sync {0} orders to Salla?', [salla_orders.length]),
        function() {
            frappe.call({
                method: 'salla_integration.api.orders.bulk_sync_orders',
                args: {
                    sales_order_names: salla_orders.map(doc => doc.name)
                },
                freeze: true,
                freeze_message: __('Syncing orders to Salla...'),
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __('Sync Completed'),
                            message: __('Successfully synced {0} out of {1} orders', 
                                       [r.message.success, salla_orders.length]),
                            indicator: 'green'
                        });
                        listview.refresh();
                    }
                }
            });
        }
    );
}

function bulk_update_status_in_salla(listview) {
    let selected_docs = listview.get_checked_items();
    
    if (selected_docs.length === 0) {
        frappe.msgprint(__('Please select orders'));
        return;
    }
    
    // Filter only submitted Salla orders
    let salla_orders = selected_docs.filter(doc => doc.salla_order_id && doc.docstatus === 1);
    
    if (salla_orders.length === 0) {
        frappe.msgprint(__('Please select submitted Salla orders'));
        return;
    }
    
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
                description: __('This will update all {0} selected orders', [salla_orders.length])
            }
        ],
        primary_action_label: __('Update Status'),
        primary_action: function(values) {
            let selected_label = values.status;
            let selected_status = status_options.find(opt => opt.label === selected_label);
            
            if (!selected_status) {
                frappe.msgprint(__('Invalid status selected'));
                return;
            }
            
            frappe.call({
                method: 'salla_integration.api.orders.bulk_update_status',
                args: {
                    sales_order_names: salla_orders.map(doc => doc.name),
                    status: selected_status.value
                },
                freeze: true,
                freeze_message: __('Updating status in Salla...'),
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __('Status Updated'),
                            message: __('Successfully updated {0} out of {1} orders', 
                                       [r.message.success, salla_orders.length]),
                            indicator: 'green'
                        });
                        listview.refresh();
                        d.hide();
                    }
                }
            });
        }
    });
    
    d.show();
}

function bulk_refresh_from_salla(listview) {
    let selected_docs = listview.get_checked_items();
    
    if (selected_docs.length === 0) {
        frappe.msgprint(__('Please select orders'));
        return;
    }
    
    // Filter only Salla orders
    let salla_orders = selected_docs.filter(doc => doc.salla_order_id);
    
    if (salla_orders.length === 0) {
        frappe.msgprint(__('None of the selected orders are from Salla'));
        return;
    }
    
    frappe.confirm(
        __('Refresh {0} orders from Salla?', [salla_orders.length]),
        function() {
            frappe.call({
                method: 'salla_integration.api.orders.bulk_refresh_orders',
                args: {
                    sales_order_names: salla_orders.map(doc => doc.name)
                },
                freeze: true,
                freeze_message: __('Refreshing from Salla...'),
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __('Refresh Completed'),
                            message: __('Successfully refreshed {0} orders', [r.message.success]),
                            indicator: 'green'
                        });
                        listview.refresh();
                    }
                }
            });
        }
    );
}

function add_salla_stats(listview) {
    // Add quick stats for Salla orders
    frappe.call({
        method: 'salla_integration.api.orders.get_salla_order_stats',
        callback: function(r) {
            if (r.message) {
                let stats = r.message;
                let stats_html = `
                    <div class="salla-stats" style="background: #f0f4ff; padding: 10px; border-radius: 4px; margin: 10px 0; display: flex; gap: 15px;">
                        <div>
                            <strong>${stats.total_today || 0}</strong>
                            <small style="color: #718096; display: block;">Today's Orders</small>
                        </div>
                        <div>
                            <strong style="color: #10b981;">${stats.synced || 0}</strong>
                            <small style="color: #718096; display: block;">Synced</small>
                        </div>
                        <div>
                            <strong style="color: #ef4444;">${stats.failed || 0}</strong>
                            <small style="color: #718096; display: block;">Failed</small>
                        </div>
                        <div>
                            <strong style="color: #f59e0b;">${stats.pending || 0}</strong>
                            <small style="color: #718096; display: block;">Pending Sync</small>
                        </div>
                    </div>
                `;
                
                listview.page.add_inner_message(stats_html);
            }
        }
    });
}

// Add custom CSS for list view
$(document).ready(function() {
    if (!$('style#salla-order-list-style').length) {
        $('head').append(`
            <style id="salla-order-list-style">
                .list-row-container[data-salla-order-id]:not([data-salla-order-id=""]) {
                    border-left: 3px solid #667eea;
                }
                .list-row-container[data-salla-order-id]:not([data-salla-order-id=""]):hover {
                    border-left-color: #5568d3;
                }
                .salla-stats {
                    animation: slideDown 0.3s ease-out;
                }
                @keyframes slideDown {
                    from {
                        opacity: 0;
                        transform: translateY(-10px);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0);
                    }
                }
            </style>
        `);
    }
});