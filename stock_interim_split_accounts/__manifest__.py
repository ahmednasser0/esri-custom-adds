{
    'name': "Stock Interim Split Accounts",

    'summary': "Separate interim accounts for purchase receipts and sale deliveries",

    'description': """
Use a dedicated interim (GRNI) account for purchases and a dedicated interim
account for sales in perpetual (Anglo-Saxon) inventory valuation:

* Receipt:        Dr. Stock Valuation      / Cr. Purchase Interim
* Vendor Bill:    Dr. Purchase Interim     / Cr. Payables
* Delivery:       Dr. Sales Interim        / Cr. Stock Valuation
* Customer Inv.:  Dr. COGS                 / Cr. Sales Interim
    """,

    'author': "Ahmad and Nouran",
    'website': "",

    'category': 'Inventory/Inventory',
    'version': '18.0.1.0.0',
    'license': 'LGPL-3',

    'depends': ['stock_account', 'purchase_stock', 'sale_stock'],

    'data': [
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
}
