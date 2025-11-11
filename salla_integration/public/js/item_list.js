// Copyright (c) 2025, Your Company
// License: MIT

frappe.listview_settings['Item'] = {
    add_fields: ["salla_product_id", "salla_store", "salla_last_synced"],
    
    get_indicator: function(doc) {
        // Show indicator if item is synced with Salla
        if (!doc || !doc.salla_product_id) {
            return null;
        }

        if (!doc.salla_last_synced) {
            return [__("Salla (Not Synced)"), "orange"];
        }

        let last_synced = frappe.datetime.str_to_obj(doc.salla_last_synced);
        if (!(last_synced instanceof Date) || isNaN(last_synced.getTime())) {
            return [__("Salla (Not Synced)"), "orange"];
        }

        let diff_hours = (Date.now() - last_synced.getTime()) / 36e5;
        if (diff_hours < 24) {
            return [__("Salla Synced"), "green"];
        }
        return [__("Salla (Outdated)"), "orange"];
    },
    
    formatters: {
        salla_store: function(value) {
            if (value) {
                return `<span class="badge badge-info" style="font-size: 11px;">${value}</span>`;
            }
            return '';
        },
        salla_product_id: function(value) {
            if (value) {
                return `<span class="text-muted" style="font-size: 11px;">
                    <i class="fa fa-link"></i> ${value}
                </span>`;
            }
            return '';
        }
    },
    
    onload: function(listview) {
        // Add custom filter buttons
        listview.page.add_inner_button(__('Salla Synced Items'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([['Item', 'salla_product_id', '!=', '']]);
        }).addClass('btn-default');
        
        listview.page.add_inner_button(__('Not Synced with Salla'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([
                ['Item', 'salla_product_id', '=', ''],
                ['Item', 'disabled', '=', 0]
            ]);
        }).addClass('btn-default');
        
        listview.page.add_inner_button(__('Outdated Sync'), function() {
            frappe.call({
                method: 'salla_integration.api.items.get_outdated_items',
                callback: function(r) {
                    if (r.message && r.message.length > 0) {
                        listview.filter_area.clear();
                        listview.filter_area.add([
                            ['Item', 'name', 'in', r.message]
                        ]);
                    } else {
                        frappe.msgprint(__('No outdated items found'));
                    }
                }
            });
        }).addClass('btn-default');
        
        // Add bulk actions for Salla
        listview.page.add_actions_menu_item(__('Push to Salla (Bulk)'), function() {
            bulk_push_to_salla(listview);
        }, true);
        
        listview.page.add_actions_menu_item(__('Pull from Salla (Bulk)'), function() {
            bulk_pull_from_salla(listview);
        }, true);
        
        listview.page.add_actions_menu_item(__('Unlink from Salla (Bulk)'), function() {
            bulk_unlink_from_salla(listview);
        }, true);
    },
    
    // Custom buttons for each row
    button: {
        show: function(doc) {
            return !!(doc && doc.salla_product_id);
        },
        get_label: function() {
            return __('Salla');
        },
        get_description: function(doc) {
            return __('View in Salla');
        },
        action: function(doc) {
            window.open(`https://s.salla.sa/product/${doc.salla_product_id}`, '_blank');
        }
    }
};

function bulk_push_to_salla(listview) {
    let selected_docs = listview.get_checked_items();
    
    if (selected_docs.length === 0) {
        frappe.msgprint(__('Please select items to push'));
        return;
    }
    
    // Filter only Salla-linked items
    let salla_items = selected_docs.filter(doc => doc.salla_product_id);
    
    if (salla_items.length === 0) {
        frappe.msgprint(__('None of the selected items are linked to Salla'));
        return;
    }
    
    frappe.confirm(
        __('Push {0} items to Salla? This will update price and stock.', [salla_items.length]),
        function() {
            frappe.call({
                method: 'salla_integration.api.items.bulk_push_items',
                args: {
                    item_names: salla_items.map(doc => doc.name)
                },
                freeze: true,
                freeze_message: __('Pushing items to Salla...'),
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __('Push Completed'),
                            message: __('Successfully pushed {0} out of {1} items', 
                                       [r.message.success, salla_items.length]),
                            indicator: 'green'
                        });
                        listview.refresh();
                    }
                }
            });
        }
    );
}

function bulk_pull_from_salla(listview) {
    let selected_docs = listview.get_checked_items();
    
    if (selected_docs.length === 0) {
        frappe.msgprint(__('Please select items to pull'));
        return;
    }
    
    // Filter only Salla-linked items
    let salla_items = selected_docs.filter(doc => doc.salla_product_id);
    
    if (salla_items.length === 0) {
        frappe.msgprint(__('None of the selected items are linked to Salla'));
        return;
    }
    
    frappe.confirm(
        __('Pull latest data from Salla for {0} items?', [salla_items.length]),
        function() {
            frappe.call({
                method: 'salla_integration.api.items.bulk_pull_items',
                args: {
                    item_names: salla_items.map(doc => doc.name)
                },
                freeze: true,
                freeze_message: __('Pulling from Salla...'),
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __('Pull Completed'),
                            message: __('Successfully pulled {0} out of {1} items', 
                                       [r.message.success, salla_items.length]),
                            indicator: 'green'
                        });
                        listview.refresh();
                    }
                }
            });
        }
    );
}

function bulk_unlink_from_salla(listview) {
    let selected_docs = listview.get_checked_items();
    
    if (selected_docs.length === 0) {
        frappe.msgprint(__('Please select items to unlink'));
        return;
    }
    
    // Filter only Salla-linked items
    let salla_items = selected_docs.filter(doc => doc.salla_product_id);
    
    if (salla_items.length === 0) {
        frappe.msgprint(__('None of the selected items are linked to Salla'));
        return;
    }
    
    frappe.confirm(
        __('Unlink {0} items from Salla? This will NOT delete products from Salla.', [salla_items.length]),
        function() {
            frappe.call({
                method: 'salla_integration.api.items.bulk_unlink_items',
                args: {
                    item_names: salla_items.map(doc => doc.name)
                },
                freeze: true,
                freeze_message: __('Unlinking items...'),
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __('Unlink Completed'),
                            message: __('Successfully unlinked {0} items', [r.message.success]),
                            indicator: 'orange'
                        });
                        listview.refresh();
                    }
                }
            });
        }
    );
}

// Add custom CSS for list view
$(document).ready(function() {
    if (!$('style#salla-item-list-style').length) {
        $('head').append(`
            <style id="salla-item-list-style">
                .list-row-container[data-salla-product-id]:not([data-salla-product-id=""]) {
                    border-left: 3px solid #667eea;
                }
                .list-row-container[data-salla-product-id]:not([data-salla-product-id=""]):hover {
                    border-left-color: #5568d3;
                }
            </style>
        `);
    }
});