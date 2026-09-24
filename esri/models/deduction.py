from datetime import date, timedelta
from odoo import fields, models, api
from .models import classify_day

UTC_OFFSET = 3.0
WEEKDAYS   = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Sunday'}

LATE_GRACE_MINUTES  = 15   # gap <= this many minutes -> quarter day (free once/month)
LATE_HALF_DAY_LIMIT = 60   # gap <= 60 minutes -> half day


class Deduction(models.Model):
    _name        = 'deduction'
    _description = 'Employee Daily Deduction'
    _inherit     = ['mail.thread', 'mail.activity.mixin']
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id   = fields.Many2one('hr.employee', string='Employee', ondelete='cascade')
    date          = fields.Date(string='Date')
    employee_rule = fields.Selection(
        related='employee_id.employee_rule',
        string='Employee Rule',
        store=True,
        readonly=True,
    )
    deducted_days = fields.Float(
        string='Deducted Days',
    )
    work_entry = fields.Many2one(
        'hr.work.entry',
        string='Work Entry',
    )

    state = fields.Selection(
        [
            ('draft',    'Draft'),
            ('posted',   'Posted'),
            ('warning',  'Warning'),
            ('archived', 'Archived'),
        ],
        string='Status',
        default='draft',
        required=True,
    )

    core_check = fields.Float(
        string='Core Check',
        compute='_compute_checks',
        help="الوقت الغير مغطى (لا حضور ولا إجازة معتمدة) داخل الفترة الأساسية "
             "من Late Check-in Limit إلى Early Check-out Limit.",
    )
    daily_check = fields.Float(
        string='Daily Check',
        compute='_compute_checks',
        help="الفرق بين ساعات اليوم الكامل والـ Actual Worked Hours (صفر لو الموظف كمل يومه).",
    )
    deduction = fields.Float(
        string='Deduction',
        compute='_compute_checks',
        help="الخصم: الأكبر بين Core Check و Daily Check مطبق عليه شرائح "
             "ربع/نص/يوم كامل، والغياب الكامل = يوم وربع. "
             "أيام العطلات الرسمية بدون خصم.",
    )

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @api.model
    def _get_core_window(self, employee):
        cal        = employee.resource_calendar_id
        late_limit = cal.late_check_in_limit   if cal and cal.late_check_in_limit   else 11.0
        early_lim  = cal.early_check_out_limit if cal and cal.early_check_out_limit else 15.0
        return late_limit, early_lim

    @api.model
    def _covered_intervals(self, employee, target_date):
        """
        Attendance intervals + hour-based approved leave intervals (local
        hours). Day-based leaves (no explicit hours) are returned separately
        as a plain hour amount.
        """
        intervals = []

        atts = self.env['hr.attendance'].sudo().search([
            ('employee_id',     '=', employee.id),
            ('attendance_date', '=', target_date),
        ])
        for att in atts:
            if not att.check_in:
                continue
            start = att.check_in.hour + att.check_in.minute / 60.0 + UTC_OFFSET
            end   = (att.check_out.hour + att.check_out.minute / 60.0 + UTC_OFFSET) \
                if att.check_out else 24.0
            if end > start:
                intervals.append((start, end))

        dayleave_hours = 0.0
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id',       '=', employee.id),
            ('request_date_from', '<=', target_date),
            ('request_date_to',   '>=', target_date),
            ('state',             '=',  'validate'),
        ])
        Overtime = self.env['hr.overtime']
        for leave in leaves:
            start = end = 0.0
            if getattr(leave, 'request_unit_hours', False):
                try:
                    start = float(getattr(leave, 'request_hour_from', 0) or 0)
                    end   = float(getattr(leave, 'request_hour_to',   0) or 0)
                except (TypeError, ValueError):
                    start = end = 0.0
            if end > start:
                intervals.append((start, end))
            else:
                dayleave_hours += Overtime._leave_day_hours(leave)

        return intervals, dayleave_hours
    @api.model
    def _uncovered_in_window(self, intervals, win_start, win_end):
        """Length of [win_start, win_end] not covered by the given intervals."""
        clipped = []
        for start, end in intervals:
            s, e = max(start, win_start), min(end, win_end)
            if e > s:
                clipped.append((s, e))
        clipped.sort()
        covered = 0.0
        cursor  = win_start
        for s, e in clipped:
            if e <= cursor:
                continue
            covered += e - max(s, cursor)
            cursor   = max(cursor, e)
        return max((win_end - win_start) - covered, 0.0)

    @api.model
    def _day_gap(self, employee, target_date):
        """(core_check, daily_check) for one employee/date."""
        cal = employee.resource_calendar_id
        hpd = cal.daily_work_hours if cal and cal.daily_work_hours else 8.0

        win_start, win_end = self._get_core_window(employee)
        intervals, dayleave_hours = self._covered_intervals(employee, target_date)

        core = self._uncovered_in_window(intervals, win_start, win_end)
        core = max(core - dayleave_hours, 0.0)

        actual = self.env['hr.overtime']._get_actual_worked_hours_for(employee, target_date)
        daily  = max(hpd - actual, 0.0)

        return core, daily

    @api.model
    def _grace_used_this_month(self, employee, target_date):
        """True if a small gap (1-15 min) already happened earlier this month."""
        cal = employee.resource_calendar_id
        day = target_date.replace(day=1)
        while day < target_date:
            if classify_day(cal, day.strftime('%A')) == 'weekday' and \
                    not self.env['hr.overtime']._is_public_holiday(day):
                core, daily = self._day_gap(employee, day)
                gap = max(core, daily)
                if 0 < gap * 60 <= LATE_GRACE_MINUTES:
                    return True
            day += timedelta(days=1)
        return False

    # ─── Compute ─────────────────────────────────────────────────────────────

    @api.depends('employee_id', 'date')
    def _compute_checks(self):
        cfg          = self.env['esri.config'].get_config()
        quarter_rate = cfg.deduction_quarter_rate  or 0.25
        half_rate    = cfg.deduction_half_rate     or 0.5
        full_rate    = cfg.deduction_full_rate     or 1.0
        absence_rate = cfg.absence_deduction_rate  or 1.25
        company_hours = cfg.company_day_hours      or 8.0
        Overtime     = self.env['hr.overtime']

        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.core_check  = 0.0
                rec.daily_check = 0.0
                rec.deduction   = 0.0
                continue

            cal = rec.employee_id.resource_calendar_id
            hpd = cal.daily_work_hours if cal and cal.daily_work_hours else 8.0

            core, daily = self._day_gap(rec.employee_id, rec.date)
            rec.core_check  = core
            rec.daily_check = daily

            day_kind = classify_day(cal, rec.date.strftime('%A'))

            # No deduction on weekends or public holidays
            if day_kind != 'weekday' or Overtime._is_public_holiday(rec.date):
                rec.deduction = 0.0
                continue

            # Full absence -> one day and a quarter
            # (attendance policy is still evaluated against the employee's
            # own Working Schedule hpd; only the deducted AMOUNT uses the
            # company-wide Company Day Hours)
            if daily >= hpd:
                rec.deduction = absence_rate * company_hours
                continue

            gap_minutes = max(core, daily) * 60.0
            if gap_minutes <= 0:
                rec.deduction = 0.0
            elif gap_minutes <= LATE_GRACE_MINUTES:
                if self._grace_used_this_month(rec.employee_id, rec.date):
                    rec.deduction = quarter_rate * company_hours
                else:
                    rec.deduction = 0.0   # free once per month
            elif gap_minutes <= LATE_HALF_DAY_LIMIT:
                rec.deduction = half_rate * company_hours
            else:
                rec.deduction = full_rate * company_hours

    # ─── Daily record creation ───────────────────────────────────────────────

    _sql_constraints = [
        ('unique_employee_date_deduction', 'UNIQUE(employee_id, date)',
         'A deduction record already exists for this employee on this date.'),
    ]

    @api.model
    def _ensure_record(self, employee_id, target_date):
        """Create the (employee, date) record if it does not exist yet."""
        existing = self.sudo().search([
            ('employee_id', '=', employee_id),
            ('date',        '=', target_date),
        ], limit=1)
        if not existing:
            existing = self.sudo().create({
                'employee_id': employee_id,
                'date':        target_date,
            })
        return existing

    @api.model
    def _create_deductions_for_date(self, target_date):
        employees = self.env['hr.employee'].sudo().search([('active', '=', True)])
        for emp in employees:
            self._ensure_record(emp.id, target_date)

    @api.model
    def _cron_create_daily_deductions(self):
        self._create_deductions_for_date(date.today() - timedelta(days=1))

    # ─── Status bar transitions ────────────────────────────────────────────────

    def action_post(self):
        self.write({'state': 'posted'})

    def action_warning(self):
        self.write({'state': 'warning'})

    def action_set_to_draft(self):
        self.write({'state': 'draft'})

    def action_archive_record(self):
        self.write({'state': 'archived'})

    # ─── Placeholder actions (logic to be filled in later) ─────────────────

    def action_run_post_deduction(self):
        pass

    def action_run_work_entry(self):
        pass
