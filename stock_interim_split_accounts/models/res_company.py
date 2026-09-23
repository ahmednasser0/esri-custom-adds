from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    purchase_interim_account_id = fields.Many2one(
        'account.account',
        string="Purchase Interim Account",
        check_company=True,
        help="Credited on vendor receipts and debited on vendor bills "
             "(Goods Received Not Invoiced).",
    )
    sale_interim_account_id = fields.Many2one(
        'account.account',
        string="Sales Interim Account",
        check_company=True,
        help="Debited on customer deliveries and credited against COGS on "
             "customer invoices (Goods Delivered Not Invoiced).",
    )

    @api.constrains('purchase_interim_account_id', 'sale_interim_account_id', 'account_stock_valuation_id')
    def _check_interim_accounts(self):
        for company in self:
            purchase = company.purchase_interim_account_id
            sale = company.sale_interim_account_id
            if purchase and purchase == sale:
                raise ValidationError(_(
                    "The Purchase Interim Account and the Sales Interim Account "
                    "must be different."))
            if company.account_stock_valuation_id and company.account_stock_valuation_id in (purchase | sale):
                raise ValidationError(_(
                    "The interim accounts must be different from the Stock "
                    "Valuation Account."))
