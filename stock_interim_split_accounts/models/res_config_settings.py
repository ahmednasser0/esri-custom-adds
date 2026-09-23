from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    purchase_interim_account_id = fields.Many2one(
        related='company_id.purchase_interim_account_id', readonly=False)
    sale_interim_account_id = fields.Many2one(
        related='company_id.sale_interim_account_id', readonly=False)
