from odoo import Command, fields, models
from odoo.tools import float_is_zero


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_interim_account(self):
        """Return the interim account used as counterpart of the stock
        valuation account for this move, or an empty recordset.

        * vendor receipts / returns to vendor   -> Purchase Interim Account
        * customer deliveries / customer returns -> Sales Interim Account

        A valuation account set on the vendor/customer location keeps the
        standard behaviour.
        """
        self.ensure_one()
        no_account = self.env['account.account']
        if self.location_id.valuation_account_id or self.location_dest_id.valuation_account_id:
            return no_account
        if self._is_dropshipped() or self._is_dropshipped_returned():
            return no_account
        usages = (self.location_id.usage, self.location_dest_id.usage)
        if 'supplier' in usages:
            return self.company_id.purchase_interim_account_id
        if 'customer' in usages:
            return self.company_id.sale_interim_account_id
        return no_account

    def _get_stock_valuation_account(self):
        self.ensure_one()
        return self.product_id.with_company(self.company_id)._get_product_accounts()['stock_valuation']

    def _should_create_account_move(self):
        if super()._should_create_account_move():
            return True
        return bool(
            self.product_id.is_storable
            and self.is_valued
            and self.product_id.with_company(self.company_id).valuation == 'real_time'
            and not float_is_zero(self.quantity, precision_rounding=self.product_uom.rounding)
            and self._get_interim_account()
        )

    def _get_account_move_line_vals(self):
        interim_account = self._get_interim_account()
        if not interim_account:
            return super()._get_account_move_line_vals()
        stock_account = self._get_stock_valuation_account()
        if self.is_in:
            # Receipt / customer return: Dr. Stock Valuation, Cr. Interim
            debit_acc, credit_acc = stock_account, interim_account
        else:
            # Delivery / return to vendor: Dr. Interim, Cr. Stock Valuation
            debit_acc, credit_acc = interim_account, stock_account
        return self._prepare_interim_aml_vals(debit_acc, credit_acc, self._get_aml_value())

    def _prepare_interim_aml_vals(self, debit_acc, credit_acc, value, label=None):
        self.ensure_one()
        name = label or (self.reference + ' - ' + self.product_id.name)
        return [{
            'account_id': credit_acc.id,
            'name': name,
            'debit': 0,
            'credit': value,
            'product_id': self.product_id.id,
        }, {
            'account_id': debit_acc.id,
            'name': name,
            'debit': value,
            'credit': 0,
            'product_id': self.product_id.id,
        }]

    def _set_value(self, correction_quantity=None):
        """When the value of an already posted receipt changes (e.g. the vendor
        bill price differs from the PO price for FIFO/AVCO products), post the
        difference between the Stock Valuation and the Purchase Interim
        accounts so that both stay in line with the inventory valuation."""
        posted_moves = self.filtered(
            lambda m: m.account_move_id and m.is_in and m._get_interim_account())
        old_values = {move.id: move.value for move in posted_moves}
        res = super()._set_value(correction_quantity=correction_quantity)
        for move in posted_moves:
            delta = move.value - old_values[move.id]
            if move.company_id.currency_id.is_zero(delta):
                continue
            stock_account = move._get_stock_valuation_account()
            interim_account = move._get_interim_account()
            if delta > 0:
                debit_acc, credit_acc = stock_account, interim_account
            else:
                debit_acc, credit_acc = interim_account, stock_account
            label = self.env._("%(ref)s - %(product)s (revaluation)",
                               ref=move.reference, product=move.product_id.name)
            self.env['account.move'].sudo().create({
                'ref': label,
                'partner_id': move._get_partner_id_for_valuation_lines(),
                'journal_id': move.company_id.account_stock_journal_id.id,
                'company_id': move.company_id.id,
                'date': fields.Date.context_today(move),
                'line_ids': [
                    Command.create(vals)
                    for vals in move._prepare_interim_aml_vals(debit_acc, credit_acc, abs(delta), label)
                ],
            })._post()
        return res
