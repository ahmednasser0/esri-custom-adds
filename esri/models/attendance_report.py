from datetime import date, timedelta
from odoo import fields, models, api
from .models import classify_day, employee_tz, local_hour


class AttendanceReport(models.Model):
    _name        = 'attendance.report'
    _description = 'Employee Attendance Report'
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade',
    )
    date = fields.Date(string='Date', required=True)
    department_id = fields.Many2one(
        'hr.department',
        string='Department',
        related='employee_id.department_id',
        store=True,
        readonly=True,
    )

    # Computed AND stored. The sources - attendance, leaves, the deduction and
    # the holiday calendar - are reached by search() and so are invisible to
    # the dependency graph; _ensure_record calls the compute directly, and runs
    # on every path that can change the day.
    check_in = fields.Float(
        string='Check-in',
        compute='_compute_values', store=True,
        help="The first check-in of the day on the employee's own clock",
    )
    check_out = fields.Float(
        string='Check-out',
        compute='_compute_values', store=True,
        help="The last check-out of the day on the employee's own clock",
    )
    leave_type_id = fields.Many2one(
        'hr.leave.type',
        string='Leave Type',
        compute='_compute_values', store=True,
        help="The approved time off covering this day, where there is one",
    )
    start_from = fields.Float(
        string='Start From',
        compute='_compute_values', store=True,
        help="The hour the approved time off starts",
    )
    end_to = fields.Float(
        string='End To',
        compute='_compute_values', store=True,
        help="The hour the approved time off ends",
    )
    actual_worked_hours = fields.Float(
        string='Actual Worked Hours',
        compute='_compute_values', store=True,
        help="Worked hours plus approved time off, past the cutoff taken off and capped, as the Daily Report counts them",
    )
    daily_check = fields.Float(
        string='Daily Check',
        compute='_compute_values', store=True,
        help="The hours missing from a full day",
    )
    deduction = fields.Float(
        string='Deduction',
        compute='_compute_values', store=True,
        help="The deduction recorded against this day, in hours",
    )
    monthly_permission = fields.Float(
        string='Monthly Permission',
        compute='_compute_values', store=True,
        help="What is left of the configured monthly permission time off on this day, in hours",
    )
    comment = fields.Selection(
        [
            ('working_day',    'Working Day'),
            ('weekend',        'Weekend'),
            ('public_holiday', 'Public Holiday'),
        ],
        string='Comment',
        compute='_compute_values', store=True,
        help="What kind of day this is",
    )

    _sql_constraints = [
        ('unique_employee_date_attendance_report', 'UNIQUE(employee_id, date)',
         'An attendance report already exists for this employee on this date.'),
    ]

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @api.model
    def _attendance_bounds(self, employee, target_date):
        """First check-in and last check-out of the day, as local hours."""
        atts = self.env['hr.attendance'].sudo().search([
            ('employee_id',     '=', employee.id),
            ('attendance_date', '=', target_date),
        ])
        tz = employee_tz(employee)

        first_in, last_out = 0.0, 0.0
        for att in atts:
            if att.check_in:
                hour = local_hour(att.check_in, tz)
                first_in = hour if not first_in else min(first_in, hour)
            if att.check_out:
                last_out = max(last_out, local_hour(att.check_out, tz))
        return first_in, last_out

    @api.model
    def _leave_of_day(self, employee, target_date):
        """
        The approved time off covering the day, with the hours it runs between.

        Where more than one covers the day the earliest is reported: the column
        names a single type, and a day with two approved leaves on it is a
        thing to look at on the Time Off screen rather than to squeeze in here.
        """
        leaves = self.env['hr.overtime']._get_approved_leaves(employee, target_date)
        if not leaves:
            return False, 0.0, 0.0

        leave = leaves.sorted(key=lambda l: (l.request_date_from, l.id))[0]

        hour_from = hour_to = 0.0
        if getattr(leave, 'request_unit_hours', False):
            try:
                hour_from = float(leave.request_hour_from or 0.0)
                hour_to   = float(leave.request_hour_to   or 0.0)
            except (TypeError, ValueError):
                hour_from = hour_to = 0.0

        return leave.holiday_status_id, hour_from, hour_to

    @api.model
    def _day_kind(self, employee, target_date):
        if self.env['hr.overtime']._is_public_holiday(target_date):
            return 'public_holiday'
        cal = employee.resource_calendar_id
        if classify_day(cal, target_date.strftime('%A')) != 'weekday':
            return 'weekend'
        return 'working_day'

    @api.model
    def _deduction_figures(self, employee, target_date):
        """(daily_check, deduction) from the day's deduction record."""
        record = self.env['deduction'].sudo().search([
            ('employee_id', '=', employee.id),
            ('date',        '=', target_date),
        ], limit=1)
        if not record:
            return 0.0, 0.0
        return record.daily_check or 0.0, record.deduction or 0.0

    # ─── Compute ─────────────────────────────────────────────────────────────

    @api.depends('employee_id', 'date')
    def _compute_values(self):
        cfg             = self.env['esri.config'].sudo().get_config()
        permission_type = cfg.monthly_permission_type_id
        Balance         = self.env['time.off.balance']
        Overtime        = self.env['hr.overtime']

        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.check_in = rec.check_out = 0.0
                rec.leave_type_id = False
                rec.start_from = rec.end_to = 0.0
                rec.actual_worked_hours = 0.0
                rec.daily_check = rec.deduction = 0.0
                rec.monthly_permission = 0.0
                rec.comment = False
                continue

            employee, day = rec.employee_id, rec.date

            rec.check_in, rec.check_out = self._attendance_bounds(employee, day)

            leave_type, hour_from, hour_to = self._leave_of_day(employee, day)
            rec.leave_type_id = leave_type.id if leave_type else False
            rec.start_from    = hour_from
            rec.end_to        = hour_to

            rec.actual_worked_hours = Overtime._get_actual_worked_hours_for(employee, day)
            rec.daily_check, rec.deduction = self._deduction_figures(employee, day)
            rec.monthly_permission = Balance._balance_hours(employee, permission_type, day)
            rec.comment = self._day_kind(employee, day)

    # ─── Daily record creation ───────────────────────────────────────────────

    @api.model
    def _ensure_record(self, employee_id, target_date):
        """Create the (employee, date) row, or refresh the one already there."""
        existing = self.sudo().search([
            ('employee_id', '=', employee_id),
            ('date',        '=', target_date),
        ], limit=1)

        if not existing:
            return self.sudo().create({
                'employee_id': employee_id,
                'date':        target_date,
            })

        existing._compute_values()
        return existing

    @api.model
    def _create_reports_for_date(self, target_date):
        employees = self.env['hr.employee'].sudo().search([('active', '=', True)])
        for emp in employees:
            self._ensure_record(emp.id, target_date)

    @api.model
    def _cron_create_reports(self):
        self._create_reports_for_date(date.today() - timedelta(days=1))

    def unlink(self):
        """Deleting a row rebuilds it, the way the other daily records do."""
        days = {
            (rec.employee_id.id, rec.date)
            for rec in self
            if rec.employee_id and rec.date
        }
        res = super().unlink()
        for employee_id, day in days:
            self._ensure_record(employee_id, day)
        return res
