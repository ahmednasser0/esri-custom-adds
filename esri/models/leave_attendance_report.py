from odoo import models, fields, api


class HrLeaveAttendanceReport(models.Model):
    _inherit = 'hr.leave.attendance.report'

    total_deduction_hours = fields.Float(
        string='Deduction Hours',
        compute='_compute_total_deduction_hours',
        help="Total deduction hours for this employee on this date.",
    )

    @api.depends('attendance_ids')
    def _compute_total_deduction_hours(self):
        for rec in self:
            rec.total_deduction_hours = sum(rec.attendance_ids.mapped('deduction_hours'))
