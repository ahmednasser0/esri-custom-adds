from odoo import models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _get_product_accounts(self):
        """Replace the stock input/output accounts with the company-level
        purchase/sales interim accounts when they are configured.

        Both the stock valuation entries (receipts / deliveries) and the
        invoice lines (vendor bills / COGS on customer invoices) read the
        interim accounts from here, so both sides stay consistent and the
        interim accounts reconcile.
        """
        accounts = super()._get_product_accounts()
        company = self.env.company
        if company.purchase_interim_account_id:
            accounts['stock_input'] = company.purchase_interim_account_id
        if company.sale_interim_account_id:
            accounts['stock_output'] = company.sale_interim_account_id
        return accounts
