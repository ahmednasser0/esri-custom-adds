from odoo import models, fields, api


class EsriConfig(models.Model):
    _name = 'esri.config'
    _description = 'Esri HR Configurations'

    name = fields.Char(default='Esri HR Configurations', readonly=True)

    overtime_rate_before_sunset = fields.Float(
        string='Overtime Rate Before Sunset',
        default=1.35,
        required=True,
        help="Hourly rate applied before sunset",
    )
    overtime_rate_after_sunset = fields.Float(
        string='Overtime Rate After Sunset',
        default=1.70,
        required=True,
        help="Hourly rate applied after sunset",
    )
    saturday_first_rate = fields.Float(
        string='Saturday First Rate (1–3 hrs)',
        default=0.5,
        required=True,
        help="The first 4 hours are rounded up to half a day.",
    )
    saturday_second_rate = fields.Float(
        string='Saturday Second Rate (4–8 hrs)',
        default=1.0,
        required=True,
        help="The Second 4 hours are rounded up to a Full day.",
    )
    friday_rate = fields.Float(
        string='Friday Rate',
        default=2.0,
        required=True,
        help="The day is calculated as a decimal value Any hours recorded on public holidays or the first weekend day are multiplied by the number of days",
    )
    overtime_cutoff = fields.Float(
        string='Overtime Cutoff Time',
        default=19.0,
        required=True,
        help="The hours are calculated as a decimal value based on a 24-hour day The maximum time considered for overtime calculation and for calculate Actual Worked Hours",
    )

    max_worked_hours = fields.Float(
        string='Max Worked Hours',
        default=12.0,
        required=True,
        help="The hours are calculated as a decimal value based on a 24-hour day The maximum number of working hours that can be recorded for an employee per day, used in the Daily Report",
    )

    overtime_request_type_id = fields.Many2one(
        'hr.leave.type',
        string='Overtime Request Time Off Type',
        ondelete='set null',
        help="The time-off type used as an additional authorization for staff and office assistant",
    )

    annual_leave_type_id = fields.Many2one(
        'hr.leave.type',
        string='Annual Leave Time Off Type',
        ondelete='set null',
        help="The time-off type whose balance the Time Off Balance report follows, normally Regular Annual Leave. Without it the report has no balance to read and every row reads zero",
    )

    # ─── Deduction rates (fraction of the daily work hours) ─────────────────

    company_day_hours = fields.Float(
        string='Company Day Hours',
        default=8.0,
        required=True,
        help="The number of daily hours used to calculate actual deductions",
    )
    deduction_quarter_rate = fields.Float(
        string='First Tier Deduction Rate (1–15 min)',
        default=0.25,
        required=True,
        help="The day is calculated as a decimal value The deduction rate applied to daily working hours for lateness or early check-out of 1–15 minutes. For example, 0.25 represents a quarter-day deduction",
    )
    deduction_half_rate = fields.Float(
        string='Second Tier Deduction Rate (16–59 min)',
        default=0.5,
        required=True,
        help="The day is calculated as a decimal value The deduction rate applied to daily working hours for lateness or early check-out of 16–59 minutes. For example, 0.5 represents a half-day deduction",
    )
    deduction_full_rate = fields.Float(
        string='Third Tier Deduction Rate (60 min or more)',
        default=1.0,
        required=True,
        help="The day is calculated as a decimal value The deduction rate applied to daily working hours for lateness or early check-out of 60 minutes or more. For example, 1.0 represents a full-day deduction",
    )
    absence_deduction_rate = fields.Float(
        string='Absence Deduction Rate',
        default=1.25,
        required=True,
        help="The day is calculated as a decimal value The deduction rate applied to daily working hours for unauthorized absence. For example, 1.25 represents a one-and-a-quarter-day deduction",
    )

    @api.model
    def get_config(self):
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return config

    @api.model_create_multi
    def create(self, vals_list):
        existing = self.search([], limit=1)
        if existing:
            existing.write(vals_list[0] if vals_list else {})
            return existing
        return super().create(vals_list)

    def unlink(self):
        raise models.UserError("لا يمكن حذف إعدادات النظام.")
