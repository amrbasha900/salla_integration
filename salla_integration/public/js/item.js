// Copyright (c) 2025, Your Company
// License: MIT

frappe.ui.form.on('Item', {
    refresh: function(frm) {
        // Only show Salla buttons if item is linked to Salla
        if (frm.doc.salla_product_id && frm.doc.salla_store) {
            add_salla_buttons(frm);
            add_salla_indicators(frm);
        }
        
        // Add sync status indicator
        if (frm.doc.salla_last_synced) {
            show_last_sync_time(frm);
        }
    },
    
    salla_store: function(frm) {
        // When store is selected, show store info
        if (frm.doc.salla_store) {
            load_store_info(frm);
        }
    }
});

function add_salla_buttons(frm) {
    // View in Salla button
    frm.add_custom_button(__('View in Salla'), function() {
        view_in_salla(frm);
    }, __('Salla'));
    
    // Sync from Salla button
    frm.add_custom_button(__('Pull from Salla'), function() {
        sync_from_salla(frm);
    }, __('Salla'));
    
    // Push to Salla button
    frm.add_custom_button(__('Push to Salla'), function() {
        push_to_salla(frm);
    }, __('Salla'));
    
    // Unlink from Salla button
    frm.add_custom_button(__('Unlink from Salla'), function() {
        unlink_from_salla(frm);
    }, __('Salla'));
}

function add_salla_indicators(frm) {
    // Add indicator showing sync status
    let indicator_color = 'green';
    let indicator_text = 'Synced with Salla';
    
    if (!frm.doc.salla_last_synced) {
        indicator_color = 'orange';
        indicator_text = 'Not Synced Yet';
    } else {
        // Check if synced recently (within 1 hour)
        let last_synced = frappe.datetime.str_to_obj(frm.doc.salla_last_synced);
        let now = frappe.datetime.now_datetime();
        let diff_hours = frappe.datetime.get_hour_diff(now, last_synced);
        
        if (diff_hours > 24) {
            indicator_color = 'orange';
            indicator_text = 'Sync Outdated';
        }
    }
    
    frm.dashboard.add_indicator(indicator_text, indicator_color);
}

function show_last_sync_time(frm) {
    if (!frm.doc.salla_last_synced) return;
    
    let sync_time = frappe.datetime.str_to_user(frm.doc.salla_last_synced);
    let time_ago = frappe.datetime.comment_when(frm.doc.salla_last_synced);
    
    frm.dashboard.set_headline_alert(
        `<div class="salla-sync-info">
            <i class="fa fa-cloud-upload" style="color: #667eea;"></i>
            Last synced with Salla: <strong>${time_ago}</strong>
        </div>`
    );
}

function view_in_salla(frm) {
    if (!frm.doc.salla_product_id) {
        frappe.msgprint(__('No Salla Product ID found'));
        return;
    }
    
    // Open Salla product page
    // Note: Adjust URL based on actual Salla product URL structure
    let salla_url = `https://s.salla.sa/product/${frm.doc.salla_product_id}`;
    window.open(salla_url, '_blank');
}

function sync_from_salla(frm) {
    frappe.confirm(
        __('Pull latest data from Salla? This will update price, stock, and description.'),
        function() {
            frappe.call({
                method: 'salla_integration.api.items.sync_product_from_salla',
                args: {
                    item_name: frm.doc.name,
                    store_name: frm.doc.salla_store
                },
                freeze: true,
                freeze_message: __('Syncing from Salla...'),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({
                            message: __('Successfully synced from Salla'),
                            indicator: 'green'
                        }, 5);
                        frm.reload_doc();
                    } else {
                        frappe.msgprint({
                            title: __('Sync Failed'),
                            indicator: 'red',
                            message: r.message ? r.message.error : __('Unknown error occurred')
                        });
                    }
                }
            });
        }
    );
}

function push_to_salla(frm) {
    frappe.confirm(
        __('Push current price and stock to Salla?'),
        function() {
            frappe.call({
                method: 'salla_integration.api.items.push_item_to_salla',
                args: {
                    item_name: frm.doc.name
                },
                freeze: true,
                freeze_message: __('Pushing to Salla...'),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({
                            message: __('Successfully pushed to Salla'),
                            indicator: 'green'
                        }, 5);
                        frm.reload_doc();
                    } else {
                        frappe.msgprint({
                            title: __('Push Failed'),
                            indicator: 'red',
                            message: r.message ? r.message.error : __('Unknown error occurred')
                        });
                    }
                }
            });
        }
    );
}

function unlink_from_salla(frm) {
    frappe.confirm(
        __('Remove Salla link from this item? This will NOT delete the product from Salla.'),
        function() {
            frm.set_value('salla_product_id', '');
            frm.set_value('salla_store', '');
            frm.set_value('salla_sku', '');
            frm.set_value('salla_sync_hash', '');
            frm.set_value('salla_last_synced', '');
            frm.save();
            
            frappe.show_alert({
                message: __('Unlinked from Salla'),
                indicator: 'orange'
            }, 5);
        }
    );
}

function load_store_info(frm) {
    if (!frm.doc.salla_store) return;
    
    frappe.call({
        method: 'frappe.client.get_value',
        args: {
            doctype: 'Salla Store',
            filters: {name: frm.doc.salla_store},
            fieldname: ['store_name', 'is_authorized', 'warehouse', 'price_list']
        },
        callback: function(r) {
            if (r.message) {
                let store_info = r.message;
                
                if (!store_info.is_authorized) {
                    frm.dashboard.set_headline_alert(
                        `<div class="alert alert-warning">
                            <i class="fa fa-warning"></i>
                            Store "${store_info.store_name}" is not authorized. 
                            <a href="/app/salla-store/${frm.doc.salla_store}">Authorize now</a>
                        </div>`,
                        'yellow'
                    );
                }
            }
        }
    });
}

// Add custom styling
frappe.ui.form.on('Item', {
    onload: function(frm) {
        // Add custom CSS for Salla section
        if (!$('style#salla-item-style').length) {
            $('head').append(`
                <style id="salla-item-style">
                    .salla-sync-info {
                        padding: 8px 12px;
                        background: #f0f4ff;
                        border-radius: 4px;
                        display: inline-block;
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
frappe.listview_settings['Item'] = {
    ...frappe.listview_settings['Item'],
    
    get_indicator: function(doc) {
        // Show indicator if item is synced with Salla
        if (doc.salla_product_id) {
            return [__("Synced with Salla"), "green", "salla_product_id,!=,''"];
        }
        
        // Fall back to default indicator
        if (frappe.listview_settings['Item'].get_indicator) {
            return frappe.listview_settings['Item'].get_indicator(doc);
        }
    },
    
    formatters: {
        salla_store: function(value) {
            if (value) {
                return `<span class="badge badge-info">${value}</span>`;
            }
            return '';
        }
    },
    
    onload: function(listview) {
        // Add custom filter button
        listview.page.add_inner_button(__('Salla Synced Items'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([['Item', 'salla_product_id', '!=', '']]);
        });
        
        listview.page.add_inner_button(__('Not Synced with Salla'), function() {
            listview.filter_area.clear();
            listview.filter_area.add([['Item', 'salla_product_id', '=', '']]);
        });
    }
};