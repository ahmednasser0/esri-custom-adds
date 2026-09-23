{
    'name': "Stock Interim Split Accounts",

    'summary': "Separate interim accounts for purchase receipts and sale deliveries",

    'description': """
Perpetual inventory valuation with dedicated interim accounts (Odoo 19).

Purchases (Purchase Interim Account / GRNI):
* Receipt:           Dr. Stock Valuation      / Cr. Purchase Interim
* Vendor Bill:       Dr. Purchase Interim     / Cr. Payables
* Bill price change: Dr. Stock Valuation      / Cr. Purchase Interim (FIFO/AVCO revaluation)

Sales (Sales Interim Account):
* Delivery:          Dr. Sales Interim        / Cr. Stock Valuation
* Customer Invoice:  Dr. COGS                 / Cr. Sales Interim

Returns post the reverse entries on the same interim accounts.
    """,

    'author': "Ahmad and Nouran",
    'website': "",

    'category': 'Inventory/Inventory',
    'version': '19.0.1.0.1',
    'license': 'LGPL-3',

    'depends': ['stock_account', 'purchase_stock', 'sale_stock'],

    'data': [
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
}
