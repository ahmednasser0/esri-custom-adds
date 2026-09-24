from datetime import date, timedelta
from odoo import fields, models, api

UTC_OFFSET = 3.0


class DailyReport(models.Model):
    _name        = 'daily.report'
    _description = 'Employee Daily Report'
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id = fields.Many2one('hr.employee', string='Employee', required=True, ondelete='cascade')
    date        = fields.Date(string='Date', required=True)

    last_check_out = fields.Float(
        string='Last Check Out',
        compute='_compute_daily_values',
        help="الأكبر بين آخر انصراف فعلى فى اليوم ونهاية الـ time off المعتمد (وقت 'إلى').",
    )
    effective_check_out = fields.Float(
        string='Effective Check-Out',
        compute='_compute_daily_values',
        help="Last Check Out بحد أقصى الـ Overtime Cutoff من الإعدادات.",
    )
    minus_minutes = fields.Float(
        string='Minus Minutes',
        compute='_compute_daily_values',
        help="الفرق بين Last Check Out والـ Overtime Cutoff لو الانصراف بعده، وإلا صفر.",
    )
    total_worked_hours = fields.Float(
        string='Total Worked Hours',
        compute='_compute_daily_values',
        help="ساعات العمل الفعلية + الإجازات المعتمدة فى اليوم.",
    )
    actual_worked_hours = fields.Float(
        string='Actual Worked Hours',
        compute='_compute_daily_values',
        help="(Total Worked Hours - Minus Minutes) بحد أقصى Max Worked Hours من الإعدادات.",
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
        return self.env['hr.leave'].sudo().search([
            ('employee_id',       '=', rec.employee_id.id),
            ('request_date_from', '<=', rec.date),
            ('request_date_to',   '>=', rec.date),
            ('state',             '=',  'validate'),
        ])

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

            atts   = self._get_attendances(rec)
            leaves = self._get_leaves(rec)

            cal      = rec.employee_id.resource_calendar_id
            first_ci = cal.first_check_in if cal and cal.first_check_in else 7.0

            # ── Last physical check-out (local hour) ────────────────────────
            physical_out = 0.0
            worked       = 0.0
            for att in atts:
                if not att.check_in or not att.check_out:
                    continue
                checkin_local  = att.check_in.hour  + att.check_in.minute  / 60.0 + UTC_OFFSET
                checkout_local = att.check_out.hour + att.check_out.minute / 60.0 + UTC_OFFSET
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
    def _create_daily_report_for_date(self, target_date):
        employees = self.env['hr.employee'].sudo().search([('active', '=', True)])
        for emp in employees:
            self._ensure_record(emp.id, target_date)

    @api.model
    def _cron_create_daily_reports(self):
        self._create_daily_report_for_date(date.today() - timedelta(days=1))
