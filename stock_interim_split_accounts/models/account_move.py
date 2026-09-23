from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    interim_stock_move_id = fields.Many2one(
        'stock.move', string="Interim Revaluation of", index='btree_not_null', copy=False, readonly=True)

    def _post(self, soft=True):
        res = super()._post(soft)
        # Close the interim account against the receipt/delivery entries.
        invoices = self.filtered(lambda m: m.state == 'posted' and m.is_invoice(include_receipts=True))
        invoices.line_ids._get_stock_moves()._reconcile_interim_lines()
        return res

    def _stock_account_prepare_realtime_out_lines_vals(self):
        """Customer invoice COGS: credit the Sales Interim Account instead of
        the Stock Valuation Account (Dr. COGS / Cr. Sales Interim)."""
        lines_vals_list = super()._stock_account_prepare_realtime_out_lines_vals()
        for vals in lines_vals_list:
            origin_line = self.env['account.move.line'].browse(vals.get('cogs_origin_id'))
            if not origin_line:
                continue
            move = origin_line.move_id.with_company(origin_line.move_id.company_id)
            interim_account = move.company_id.sale_interim_account_id
            if not interim_account:
                continue
            stock_account = origin_line.product_id.product_tmpl_id.with_company(move.company_id) \
                .get_product_accounts(fiscal_pos=move.fiscal_position_id)['stock_valuation']
            if stock_account and vals['account_id'] == stock_account.id:
                vals['account_id'] = move.fiscal_position_id.map_account(interim_account).id
        return lines_vals_list
