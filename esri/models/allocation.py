from odoo import fields, models


class HrLeaveAllocation(models.Model):
    _inherit = 'hr.leave.allocation'

    money_deduction_hours = fields.Float(
        string='Money Deduction Hours',
        help="Set automatically when an employee has a deduction but their "
             "Annual Leave balance is exhausted: holds the deduction hours "
             "that should be deducted from salary (money) instead of being "
             "taken from leave days.",
    )