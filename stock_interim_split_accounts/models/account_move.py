from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

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
