from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Receivable/payable lines need a due date and cash/card accounts are for
# payments, so none of them can hold interim stock entries.
FORBIDDEN_INTERIM_ACCOUNT_TYPES = (
    'asset_receivable', 'liability_payable', 'asset_cash', 'liability_credit_card',
)
INTERIM_ACCOUNT_DOMAIN = "[('account_type', 'not in', %r)]" % (FORBIDDEN_INTERIM_ACCOUNT_TYPES,)


class ResCompany(models.Model):
    _inherit = 'res.company'

    purchase_interim_account_id = fields.Many2one(
        'account.account',
        string="Purchase Interim Account",
        check_company=True,
        domain=INTERIM_ACCOUNT_DOMAIN,
        help="Credited on vendor receipts and debited on vendor bills "
             "(Goods Received Not Invoiced).",
    )
    sale_interim_account_id = fields.Many2one(
        'account.account',
        string="Sales Interim Account",
        check_company=True,
        domain=INTERIM_ACCOUNT_DOMAIN,
        help="Debited on customer deliveries and credited against COGS on "
             "customer invoices (Goods Delivered Not Invoiced).",
    )

    @api.constrains('purchase_interim_account_id', 'sale_interim_account_id', 'account_stock_valuation_id')
    def _check_interim_accounts(self):
        for company in self:
            purchase = company.purchase_interim_account_id
            sale = company.sale_interim_account_id
            if (purchase | sale).filtered(
                    lambda a: a.account_type in FORBIDDEN_INTERIM_ACCOUNT_TYPES):
                raise ValidationError(_(
                    "The interim accounts cannot be Receivable, Payable, Bank/Cash "
                    "or Credit Card accounts. Use e.g. a Current Liabilities account "
                    "for the Purchase Interim and a Current Assets account for the "
                    "Sales Interim."))
            if purchase and purchase == sale:
                raise ValidationError(_(
                    "The Purchase Interim Account and the Sales Interim Account "
                    "must be different."))
            if company.account_stock_valuation_id and company.account_stock_valuation_id in (purchase | sale):
                raise ValidationError(_(
                    "The interim accounts must be different from the Stock "
                    "Valuation Account."))
