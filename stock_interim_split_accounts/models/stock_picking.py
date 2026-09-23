from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    interim_move_line_ids = fields.Many2many(
        'account.move.line',
        string="Accounting Entries",
        compute='_compute_interim_move_line_ids',
    )
    interim_move_line_count = fields.Integer(compute='_compute_interim_move_line_ids')

    @api.depends('move_ids.account_move_id', 'move_ids.interim_revaluation_move_ids')
    def _compute_interim_move_line_ids(self):
        for picking in self:
            moves = picking.move_ids
            # Receipt / delivery entries and their revaluations (both sides)
            lines = (moves.account_move_id | moves.interim_revaluation_move_ids).line_ids
            # Invoice side on the interim account, to show how it is closed
            for move in moves:
                order_line = move._get_interim_order_line()
                if order_line:
                    lines |= self.env['stock.move']._get_interim_lines_for_order_line(order_line)
            picking.interim_move_line_ids = lines.sorted(lambda l: (l.date, l.move_id.id, l.id))
            picking.interim_move_line_count = len(lines)
