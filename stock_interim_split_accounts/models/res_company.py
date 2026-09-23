from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    purchase_interim_account_id = fields.Many2one(
        'account.account',
        string="Purchase Interim Account",
        check_company=True,
        domain="[('deprecated', '=', False)]",
        help="Credited on purchase receipts and debited on vendor bills "
             "(Goods Received Not Invoiced). Overrides the Stock Input Account "
             "of product categories.",
    )
    sale_interim_account_id = fields.Many2one(
        'account.account',
        string="Sales Interim Account",
        check_company=True,
        domain="[('deprecated', '=', False)]",
        help="Debited on customer deliveries and credited on customer invoices "
             "against COGS (Goods Delivered Not Invoiced). Overrides the Stock "
             "Output Account of product categories.",
    )

    @api.constrains('purchase_interim_account_id', 'sale_interim_account_id')
    def _check_interim_accounts_differ(self):
        for company in self:
            if (company.purchase_interim_account_id
                    and company.purchase_interim_account_id == company.sale_interim_account_id):
                raise ValidationError(_(
                    "The Purchase Interim Account and the Sales Interim Account "
                    "must be different."))
