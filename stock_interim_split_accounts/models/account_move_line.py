from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _compute_account_id(self):
        """Vendor bills: debit the Purchase Interim Account instead of the
        Stock Valuation Account (Dr. Purchase Interim / Cr. Payables)."""
        super()._compute_account_id()
        for line in self:
            if not line.move_id.is_purchase_document():
                continue
            company = line.move_id.company_id
            interim_account = company.purchase_interim_account_id
            if not interim_account or not line._eligible_for_stock_account():
                continue
            if line.with_company(company).product_id.valuation != 'real_time':
                continue
            line.account_id = line.move_id.fiscal_position_id.map_account(interim_account)

    def _get_interim_cogs_lines(self):
        """Posted COGS lines on the Sales Interim Account linked to the same
        sale order lines. sale_stock only looks for them on the Stock Valuation
        Account, so they are added here to keep COGS quantities/values right on
        partial and multiple invoices."""
        self.ensure_one()
        company = self.move_id.company_id
        interim_account = self.move_id.fiscal_position_id.map_account(company.sale_interim_account_id)
        valuation_account = self.product_id.product_tmpl_id.with_company(company) \
            .get_product_accounts(fiscal_pos=self.move_id.fiscal_position_id)['stock_valuation']
        sale_lines = self.sale_line_ids
        if not interim_account or interim_account == valuation_account or not sale_lines:
            return self.env['account.move.line']
        return sale_lines.order_id.invoice_ids.filtered(
            lambda m: m.move_type in ('out_invoice', 'out_refund')
        ).line_ids.filtered(
            lambda line: line.display_type == 'cogs'
            and line.account_id == interim_account
            and line.cogs_origin_id.sale_line_ids & sale_lines
        )

    def _get_cogs_qty(self):
        interim_lines = self._get_interim_cogs_lines()
        interim_qty = sum(interim_lines.mapped(
            lambda line: line.product_uom_id._compute_quantity(line.quantity, line.product_id.uom_id)
            * (-1 if line.move_id.move_type == 'out_refund' else 1)
        ))
        return interim_qty + super()._get_cogs_qty()

    def _get_posted_cogs_value(self):
        interim_value = -sum(self._get_interim_cogs_lines().mapped('balance'))
        return interim_value + super()._get_posted_cogs_value()
