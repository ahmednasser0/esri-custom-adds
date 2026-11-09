import logging
from datetime import date, timedelta
from odoo import fields, models, api
from .models import classify_day, employee_tz, local_hour

_logger = logging.getLogger(__name__)


class AttendanceReport(models.Model):
    _name        = 'attendance.report'
    _description = 'Employee Attendance Report'
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade',
    )
    employee_code = fields.Char(
        string='Employee ID',
        related='employee_id.barcode',
        store=True,
        readonly=True,
        help="The employee's badge id, taken from the Barcode field on the employee",
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
    first_check_in = fields.Float(
        string='First Check-in',
        compute='_compute_values', store=True,
        help="The first the day starts, on the employee's own clock: the earliest check-in, or the hour his approved time off begins when that comes first",
    )
    last_check_out = fields.Float(
        string='Last Check-out',
        compute='_compute_values', store=True,
        help="The last the day runs to, on the employee's own clock: the latest check-out, or the hour his approved time off ends when that comes later",
    )
    late_minutes = fields.Float(
        string='Late Minutes',
        compute='_compute_values', store=True,
        help="How far the day's First Check-in fell past the Late Check-in Limit on the employee's Working Schedule, or zero when it did not",
    )
    early_leave_minutes = fields.Float(
        string='Early Leave Minutes',
        compute='_compute_values', store=True,
        help="The day's work hours less the time between the First Check-in and the Last Check-out and less the lateness: what is missing from the end of the day rather than the start of it",
    )
    leave_ids = fields.Many2many(
        'hr.leave',
        string='Time Off',
        compute='_compute_leave_ids',
        help="The employee's time off covering this day, whatever its status",
    )
    worked_hours = fields.Float(
        string='Worked Hours',
        compute='_compute_values', store=True,
        help="The day's worked hours as the Attendances app records them: check-in to check-out, with nothing clipped, capped, or added for time off",
    )
    attendance_hours = fields.Float(
        string='Attendance Hours',
        compute='_compute_values', store=True,
        help="The time the employee was actually clocked in for: the latest check-out less the earliest check-in, with no time off in it",
    )
    activities_hours = fields.Float(
        string='Activities Hours',
        compute='_compute_values', store=True,
        help="The hours of approved time off covering this day, added up",
    )
    total_recorded_hours = fields.Float(
        string='Total Recorded Hours',
        compute='_compute_values', store=True,
        help="The Attendance Hours and the Activities Hours together: everything the day has on record",
    )
    difference = fields.Float(
        string='Difference',
        compute='_compute_values', store=True,
        help="The day's work hours less the Total Recorded Hours. A positive figure is what the day is short; a negative one is time over and above it",
    )
    deduction_state = fields.Selection(
        [
            ('draft',    'Draft'),
            ('archived', 'Archived'),
        ],
        string='Deduction Status',
        compute='_compute_values', store=True,
        help="The status of this day's deduction record, where there is one",
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

    _unique_employee_date_attendance_report = models.Constraint(
        'UNIQUE(employee_id, date)',
        'An attendance report already exists for this employee on this date.',
    )

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @api.model
    def _punch_bounds(self, employee, target_date):
        """
        The earliest check-in and the latest check-out of the day, as local
        hours, from the attendance alone. Nothing but the clock: no time off.
        """
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
    def _leave_intervals(self, employee, target_date):
        """
        The hours each of the day's approved leaves runs between, as local
        hours on the employee's own clock, as a list of (from, to).

        Every approved leave covering the day counts, the Overtime Request
        type included: the day is being described here, not paid for. A leave
        still waiting for approval, or refused, is not the employee's day and
        does not count.

        A leave booked by the hour says its hours itself. One booked by the
        day carries them on its own start and end, so those are read and used
        for every day it covers - a leave that runs 9:00 to 17:00 is eight
        hours on each of its days, not twenty-four on the ones in the middle.

        This is the one place the day's time off is read. The First Check-in,
        the Last Check-out and the Activities Hours all come from it, so they
        can never tell different stories about the same leave.
        """
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id',       '=',  employee.id),
            ('request_date_from', '<=', target_date),
            ('request_date_to',   '>=', target_date),
            ('state',             '=',  'validate'),
        ])
        if not leaves:
            return []

        tz = employee_tz(employee)
        intervals = []
        for leave in leaves:
            hour_from = hour_to = 0.0

            if getattr(leave, 'request_unit_hours', False):
                try:
                    hour_from = float(leave.request_hour_from or 0.0)
                    hour_to   = float(leave.request_hour_to   or 0.0)
                except (TypeError, ValueError):
                    hour_from = hour_to = 0.0

            if hour_to <= hour_from and leave.date_from and leave.date_to:
                hour_from = local_hour(leave.date_from, tz)
                hour_to   = local_hour(leave.date_to,   tz)

            if hour_to > hour_from:
                intervals.append((hour_from, hour_to))
        return intervals

    @api.model
    def _leave_bounds(self, employee, target_date):
        """The first hour the day's time off starts and the last it runs to."""
        intervals = self._leave_intervals(employee, target_date)
        if not intervals:
            return 0.0, 0.0
        return min(i[0] for i in intervals), max(i[1] for i in intervals)

    @api.model
    def _attendance_bounds(self, employee, target_date):
        """
        The first the day starts and the last it runs to, as local hours.

        The attendance and the approved time off are read together: a day that
        begins with a morning's leave and is attended afterwards starts at the
        leave, and one attended first and left on leave runs to the end of the
        leave. Either side on its own answers for the whole day when it is the
        only one there.
        """
        first_in, last_out = self._punch_bounds(employee, target_date)
        leave_from, leave_to = self._leave_bounds(employee, target_date)
        if leave_from:
            first_in = leave_from if not first_in else min(first_in, leave_from)
        if leave_to:
            last_out = max(last_out, leave_to)

        return first_in, last_out

    @api.model
    def _day_kind(self, employee, target_date):
        if self.env['hr.overtime']._is_public_holiday(target_date):
            return 'public_holiday'
        cal = employee.resource_calendar_id
        if classify_day(cal, target_date.strftime('%A')) != 'weekday':
            return 'weekend'
        return 'working_day'

    @api.model
    def _worked_hours(self, employee, target_date):
        """
        The day's worked hours straight from the Attendances app.

        Every attendance on the day added up as Odoo itself measures each one,
        check-in to check-out. Nothing of this module's own reading of the day
        comes into it: no clipping at the first check-in, no cutoff, no cap,
        and no approved time off.
        """
        atts = self.env['hr.attendance'].sudo().search([
            ('employee_id',     '=', employee.id),
            ('attendance_date', '=', target_date),
        ])
        return sum(atts.mapped('worked_hours'))

    @api.model
    def _late_and_early(self, employee, first_check_in, last_check_out):
        """
        (late, early_leave) for the day, both in hours.

        Late is how far the day's start fell past the Late Check-in Limit on
        the employee's Working Schedule, and nothing at all when it did not.

        Early leave is what the day still owes once the time between its start
        and its end, and the lateness, are taken off the full working day: the
        shortfall at the end of the day rather than at the start.

        Neither goes below zero. A day that started early is not a day owed
        back, and a day that ran over is not a day left early.
        """
        cal = employee.resource_calendar_id
        late_limit = cal.late_check_in_limit if cal and cal.late_check_in_limit else 11.0
        hpd        = cal.daily_work_hours    if cal and cal.daily_work_hours    else 8.0

        late = max(first_check_in - late_limit, 0.0) if first_check_in else 0.0

        span = last_check_out - first_check_in
        if span < 0.0:
            span = 0.0
        early = max(hpd - span - late, 0.0)
        return late, early

    @api.model
    def _deduction_figures(self, employee, target_date):
        """(daily_check, deduction, state) from the day's deduction record."""
        record = self.env['deduction'].sudo().search([
            ('employee_id', '=', employee.id),
            ('date',        '=', target_date),
        ], limit=1)
        if not record:
            return 0.0, 0.0, False
        return record.daily_check or 0.0, record.deduction or 0.0, record.state or False

    @api.model
    def _activities_hours(self, employee, target_date):
        """
        The hours of approved time off covering the day, added up: each leave
        measured from where it starts to where it ends, the same reading the
        First Check-in and the Last Check-out are taken from.
        """
        return sum(end - start
                   for start, end in self._leave_intervals(employee, target_date))

    @api.model
    def _hours_per_day(self, employee):
        cal = employee.resource_calendar_id
        for value in (getattr(cal, 'hours_per_day', 0.0),
                      getattr(cal, 'daily_work_hours', 0.0)):
            if value:
                return value
        return 8.0

    @api.model
    def _allocation_hours(self, allocation, employee):
        """One allocation expressed in hours."""
        hours = getattr(allocation, 'number_of_hours_display', 0.0) or 0.0
        if hours:
            return hours
        return (allocation.number_of_days or 0.0) * self._hours_per_day(employee)

    @api.model
    def _balance_hours(self, employee, leave_type, target_date):
        """
        What is left of leave_type for employee on target_date, in hours:
        everything allocated and running on that day, less everything already
        taken by then.
        """
        if not leave_type:
            return 0.0

        allocations = self.env['hr.leave.allocation'].sudo().search([
            ('employee_id',       '=', employee.id),
            ('holiday_status_id', '=', leave_type.id),
            ('state',             '=', 'validate'),
            ('date_from',        '<=', target_date),
            '|', ('date_to', '=', False), ('date_to', '>=', target_date),
        ])
        allocated = sum(self._allocation_hours(a, employee) for a in allocations)

        # A leave counts from the day it starts; one still running on
        # target_date is counted in full, the way the balance on the Time Off
        # screen already reads it.
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id',        '=', employee.id),
            ('holiday_status_id',  '=', leave_type.id),
            ('state',              '=', 'validate'),
            ('request_date_from', '<=', target_date),
        ])
        taken = sum(l.number_of_hours or 0.0 for l in leaves)

        return allocated - taken

    # ─── Compute ─────────────────────────────────────────────────────────────

    @api.depends('employee_id', 'date')
    def _compute_leave_ids(self):
        Leave = self.env['hr.leave'].sudo()
        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.leave_ids = False
                continue
            rec.leave_ids = Leave.search([
                ('employee_id',       '=',  rec.employee_id.id),
                ('request_date_from', '<=', rec.date),
                ('request_date_to',   '>=', rec.date),
            ])

    @api.depends('employee_id', 'date')
    def _compute_values(self):
        cfg             = self.env['esri.config'].sudo().get_config()
        permission_type = cfg.monthly_permission_type_id

        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.first_check_in = rec.last_check_out = 0.0
                rec.worked_hours = 0.0
                rec.daily_check = rec.deduction = 0.0
                rec.attendance_hours = rec.activities_hours = 0.0
                rec.total_recorded_hours = rec.difference = 0.0
                rec.deduction_state = False
                rec.monthly_permission = 0.0
                rec.late_minutes = rec.early_leave_minutes = 0.0
                rec.comment = False
                continue

            employee, day = rec.employee_id, rec.date

            rec.first_check_in, rec.last_check_out = self._attendance_bounds(employee, day)

            rec.worked_hours = self._worked_hours(employee, day)
            (rec.daily_check, rec.deduction,
             rec.deduction_state) = self._deduction_figures(employee, day)

            punch_in, punch_out = self._punch_bounds(employee, day)
            rec.attendance_hours = max(punch_out - punch_in, 0.0) if punch_in and punch_out else 0.0
            rec.activities_hours = self._activities_hours(employee, day)
            rec.total_recorded_hours = rec.attendance_hours + rec.activities_hours
            cal = employee.resource_calendar_id
            hpd = cal.daily_work_hours if cal and cal.daily_work_hours else 8.0
            rec.difference = hpd - rec.total_recorded_hours

            rec.monthly_permission = self._balance_hours(employee, permission_type, day)
            rec.late_minutes, rec.early_leave_minutes = self._late_and_early(
                employee, rec.first_check_in, rec.last_check_out)
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

    @api.model
    def _recompute_day_figures(self):
        """
        Work the day's figures out again on every row.

        A stored column keeps whatever it was last given, and Odoo only fills
        one it has just added. Every column here has been worked out under a
        rule that has since changed, on a database that may already carry an
        earlier version of it, so they are all asked for again rather than
        guessed at.
        """
        try:
            rows = self.sudo().search([])
            if not rows:
                return
            for name in ('first_check_in', 'last_check_out',
                         'late_minutes', 'early_leave_minutes',
                         'attendance_hours', 'activities_hours',
                         'total_recorded_hours', 'difference',
                         'deduction_state'):
                self.env.add_to_compute(self._fields[name], rows)
            rows.flush_recordset()
        except Exception:
            _logger.warning("Could not work the Attendance report's day "
                            "figures out again", exc_info=True)

    @api.model
    def _refresh_employee_codes(self):
        """
        Work the Employee ID out again on every row.

        The column used to be filled by a compute that went looking through
        several identifier fields; it now simply follows the employee's
        barcode. A stored column keeps whatever it was last given, so rows
        written under the old rule would otherwise keep showing it.
        """
        try:
            rows = self.sudo().search([])
            if rows:
                self.env.add_to_compute(self._fields['employee_code'], rows)
                rows.flush_recordset()
        except Exception:
            _logger.warning("Could not refresh the Employee ID column",
                            exc_info=True)

    @api.model
    def _attach_to_employees_app(self):
        """
        Move the report's parent menu into the Employees app.

        The app is looked up rather than named in the xml: a menu id that has
        moved between Odoo versions stops the module loading, so the menu is
        declared on its own and placed here, and anything that goes wrong
        leaves it where it was instead of failing the upgrade.
        """
        try:
            menu = self.env.ref('esri.menu_esri_attendance_reports', raise_if_not_found=False)
            root = self.env.ref('hr.menu_hr_root', raise_if_not_found=False)
            if not root:
                # Fall back to the app menu by name.
                root = self.env['ir.ui.menu'].sudo().search([
                    ('parent_id', '=', False),
                    ('name', '=', 'Employees'),
                ], limit=1)
            if menu and root and menu.parent_id.id != root.id:
                menu.sudo().parent_id = root.id
        except Exception:
            _logger.warning("Could not move the Attendance Report menu into the "
                            "Employees app; it stays where it was declared",
                            exc_info=True)

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
