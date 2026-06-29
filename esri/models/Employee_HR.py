from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    employee_rule = fields.Selection(
        selection=[
            ('staff',            'Staff'),
            ('office_assistant', 'Office Assistant'),
            ('office_boy',       'Office Boy'),
        ],
        string='Employee Rule',
    )