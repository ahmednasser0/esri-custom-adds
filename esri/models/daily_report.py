from datetime import date, timedelta
from odoo import fields, models, api
from .models import classify_day, employee_tz, local_hour


class DailyReport(models.Model):
    _name        = 'daily.report'
    _description = 'Employee Daily Report'
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id = fields.Many2one('hr.employee', string='Employee', required=True, ondelete='cascade')
    date        = fields.Date(string='Date', required=True)

    # Computed AND stored. The values come from hr.attendance and hr.leave via
    # manual search() calls, which Odoo's dependency graph cannot see, so
    # _compute_daily_values only fires when employee_id / date change — and
    # those never change on an existing record. _ensure_record recomputes them
    # explicitly instead, and it runs on every path that can affect the result.
    # Storing them makes the values searchable, groupable and cheap to read.
    last_check_out = fields.Float(
        string='Last Check Out',
        compute='_compute_daily_values',
        store=True,
        help="Based on the latest recorded check-out time from either the Attendance or Time Off module.",
    )
    effective_check_out = fields.Float(
        string='Effective Check-Out',
        compute='_compute_daily_values',
        store=True,
        help="The latest recorded check-out time from either the Attendance or Time Off module, capped at the Overtime Cutoff time configured in the settings.",
    )
    minus_minutes = fields.Float(
        string='Minus Minutes',
        compute='_compute_daily_values',
        store=True,
        help="The time difference between Last Check Out and the Overtime Cutoff",
    )
    total_worked_hours = fields.Float(
        string='Total Worked Hours',
        compute='_compute_daily_values',
        store=True,
        help="Total actual working hours plus approved time off hours for the day",
    )
    actual_worked_hours = fields.Float(
        string='Actual Worked Hours',
        compute='_compute_daily_values',
        store=True,
        help="The actual number of working hours to be considered for calculation",
    )

    _sql_constraints = [
        ('unique_employee_date_daily_report', 'UNIQUE(employee_id, date)',
         'A daily report already exists for this employee on this date.'),
    ]

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _get_attendances(self, rec):
        return self.env['hr.attendance'].sudo().search([
            ('employee_id',     '=', rec.employee_id.id),
            ('attendance_date', '=', rec.date),
        ])

    def _get_leaves(self, rec):
        return self.env['hr.overtime']._get_approved_leaves(rec.employee_id, rec.date)

    @api.model
    def _leave_day_hours(self, leave):
        """
        Hours the leave covers for a single day. Prefer the explicit
        'from'/'to' hours (independent of the employee's working calendar,
        which is required for flexible schedules where number_of_hours
        would otherwise compute to 0). Falls back to number_of_hours,
        averaged over the leave's day span, for whole-day leaves.
        """
        if getattr(leave, 'request_unit_hours', False):
            hour_from = getattr(leave, 'request_hour_from', False)
            hour_to   = getattr(leave, 'request_hour_to',   False)
            try:
                hour_from = float(hour_from) if hour_from else 0.0
                hour_to   = float(hour_to)   if hour_to   else 0.0
            except (TypeError, ValueError):
                hour_from = hour_to = 0.0
            if hour_to > hour_from:
                return hour_to - hour_from

        total_days = (leave.request_date_to - leave.request_date_from).days + 1
        return (leave.number_of_hours or 0.0) / max(total_days, 1)

    @api.model
    def _leave_hour_to(self, leave):
        """Explicit end hour ('to') of the leave if it is hour-based, else 0."""
        if getattr(leave, 'request_unit_hours', False):
            hour_to = getattr(leave, 'request_hour_to', False)
            try:
                return float(hour_to) if hour_to else 0.0
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    # ─── Compute ─────────────────────────────────────────────────────────────

    @api.depends('employee_id', 'date')
    def _compute_daily_values(self):
        cfg       = self.env['esri.config'].get_config()
        ot_cutoff = cfg.overtime_cutoff or 19.0
        max_wh    = cfg.max_worked_hours or 12.0

        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.last_check_out      = 0.0
                rec.effective_check_out = 0.0
                rec.minus_minutes       = 0.0
                rec.total_worked_hours  = 0.0
                rec.actual_worked_hours = 0.0
                continue

            cal      = rec.employee_id.resource_calendar_id
            first_ci = cal.first_check_in if cal and cal.first_check_in else 7.0
            tz       = employee_tz(rec.employee_id)
            Overtime = self.env['hr.overtime']
            day_kind = classify_day(cal, rec.date.strftime('%A'))

            # On a weekend day the attendance and the time off are not worked
            # hours at all: only what was approved in advance on an Overtime
            # Request counts, and the day is worth exactly that. The office boy
            # is outside the scheme and keeps reading his attendance.
            if day_kind != 'weekday' and not Overtime._is_attendance_only(rec.employee_id):
                hours    = Overtime._request_hours(rec.employee_id, rec.date)
                last_out = Overtime._request_end_hour(rec.employee_id, rec.date)
                minus    = max(last_out - ot_cutoff, 0.0)
                rec.last_check_out      = last_out
                rec.effective_check_out = min(last_out, ot_cutoff) if last_out else 0.0
                rec.minus_minutes       = minus
                rec.total_worked_hours  = hours
                rec.actual_worked_hours = min(max(hours - minus, 0.0), max_wh)
                continue

            atts   = self._get_attendances(rec)
            leaves = self._get_leaves(rec)

            # ── Last physical check-out (local hour) ────────────────────────
            physical_out = 0.0
            worked       = 0.0
            for att in atts:
                if not att.check_in or not att.check_out:
                    continue
                checkin_local  = local_hour(att.check_in,  tz)
                checkout_local = local_hour(att.check_out, tz)
                checkin_effective = max(checkin_local, first_ci)
                worked += max(checkout_local - checkin_effective, 0.0)
                physical_out = max(physical_out, checkout_local)

            # ── Time-off end hour + approved time-off hours ─────────────────
            time_off_hours = 0.0
            leave_end      = 0.0
            for leave in leaves:
                day_hours       = self._leave_day_hours(leave)
                time_off_hours += day_hours
                explicit_to     = self._leave_hour_to(leave)
                if explicit_to:
                    leave_end = max(leave_end, explicit_to)
                elif day_hours > 0 and physical_out:
                    # No explicit hours: assume the leave extends after the
                    # last physical check-out by its daily share.
                    leave_end = max(leave_end, physical_out + day_hours)

            # 3) Last Check Out = max(physical check-out, time-off end)
            last_out = max(physical_out, leave_end)

            # 4) Effective Check-Out = min(last check out, overtime cutoff)
            effective_out = min(last_out, ot_cutoff) if last_out else 0.0

            # 5) Minus Minutes = excess beyond the cutoff (as time), else 0
            minus = max(last_out - ot_cutoff, 0.0)

            # 6) Total Worked Hours = worked + approved time off
            total = worked + time_off_hours

            # 7) Actual Worked Hours = min(total - minus, max worked hours)
            actual = min(max(total - minus, 0.0), max_wh)

            rec.last_check_out      = last_out
            rec.effective_check_out = effective_out
            rec.minus_minutes       = minus
            rec.total_worked_hours  = total
            rec.actual_worked_hours = actual

    # ─── Daily record creation ───────────────────────────────────────────────

    @api.model
    def _ensure_record(self, employee_id, target_date):
        """
        Create the (employee, date) record if it does not exist yet, and refresh
        its stored values otherwise.
        """
        existing = self.sudo().search([
            ('employee_id', '=', employee_id),
            ('date',        '=', target_date),
        ], limit=1)

        if not existing:
            # create() computes the stored fields on its own.
            return self.sudo().create({
                'employee_id': employee_id,
                'date':        target_date,
            })

        # Calling the compute directly is what refreshes a stored computed field
        # whose real sources (attendance, leaves) are outside its @api.depends.
        existing._compute_daily_values()
        return existing

    def unlink(self):
        """
        Deleting a daily report recalculates it rather than removing it.

        The figures are worked out again from the attendance and the leaves as
        they stand now, for today or for any day in the past, and the record
        comes back. Deleting it is how a stale row is refreshed - the daily cron
        only ever revisits yesterday, so nothing else would reach an older one.
        """
        days = {
            (rec.employee_id.id, rec.date)
            for rec in self
            if rec.employee_id and rec.date
        }

        res = super().unlink()

        for employee_id, day in days:
            self._ensure_record(employee_id, day)
        return res

    @api.model
    def _create_daily_report_for_date(self, target_date):
        employees = self.env['hr.employee'].sudo().search([('active', '=', True)])
        for emp in employees:
            self._ensure_record(emp.id, target_date)

    @api.model
    def _cron_create_daily_reports(self):
        self._create_daily_report_for_date(date.today() - timedelta(days=1))
