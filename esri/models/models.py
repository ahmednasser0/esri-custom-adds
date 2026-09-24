from odoo import models, fields, api

# ─── Constants ───────────────────────────────────────────────────────────────

WEEKDAYS       = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Sunday'}
STANDARD_HOURS = 8.0
UTC_OFFSET     = 3.0

DAY_SELECTION = [
    ('Monday',    'Monday'),
    ('Tuesday',   'Tuesday'),
    ('Wednesday', 'Wednesday'),
    ('Thursday',  'Thursday'),
    ('Friday',    'Friday'),
    ('Saturday',  'Saturday'),
    ('Sunday',    'Sunday'),
]

class ResourceCalendar(models.Model):
    _inherit = 'resource.calendar'

    daily_work_hours = fields.Float(
        string='Daily Work Hours',
        default=8.0,
        help="Number of working hours in a full day for this schedule. Used for overtime and deduction calculations.",
    )
    late_check_in_limit = fields.Float(
        string='Late Check-in Limit',
        default=11.0,
        help="Hour after which an employee is considered late (24h format). Default: 11:00 AM.",
    )
    early_check_out_limit = fields.Float(
        string='Early Check-out Limit',
        default=15.0,
        help="Hour before which leaving is considered early departure (24h format). Default: 3:00 PM.",
    )
    first_check_in = fields.Float(
        string='First Check-in Time',
        default=7.0,
        help="Earliest valid check-in hour (24h format). Attendance before this time is ignored. Default: 7:00 AM.",
    )
    last_check_out = fields.Float(
        string='Last Check-out Time',
        default=19.0,
        help="Latest valid check-out hour (24h format). Attendance after this time is capped. Default: 7:00 PM.",
    )
    sunset = fields.Float(
        string='Sunset Time',
        default=18.0,
        help="وقت الغروب لهذا الجدول (24h format). مثال: 18.5 = 6:30 مساء.",
    )
    week_start_day = fields.Selection(
        DAY_SELECTION,
        string='Week Start Day',
        default='Sunday',
        help="اليوم اللى يعتبر بداية الأسبوع لهذا الجدول.",
    )
    week_end_day = fields.Selection(
        DAY_SELECTION,
        string='Week End Day',
        default='Thursday',
        help="اليوم اللى يعتبر نهاية الأسبوع لهذا الجدول.",
    )
    weekend_first_day = fields.Selection(
        DAY_SELECTION,
        string='Weekend First Day',
        default='Friday',
        help="اليوم اللى هياخد نفس حسبة يوم الجمعة الحالية (overtime/deduction).",
    )
    weekend_second_day = fields.Selection(
        DAY_SELECTION,
        string='Weekend Second Day',
        default='Saturday',
        help="اليوم اللى هياخد نفس حسبة يوم السبت الحالية (overtime/deduction).",
    )
    number_of_days = fields.Integer(
        string='Number of Days',
    )

    def _weekend_roles(self):
        """Return (weekend_first_day, weekend_second_day), defaulting to Friday/Saturday."""
        self.ensure_one()
        first  = self.weekend_first_day  or 'Friday'
        second = self.weekend_second_day or 'Saturday'
        return first, second


def classify_day(cal, day_name):
    """
    Classify day_name against a resource.calendar's configured weekend days.
    Returns 'weekend_first' (acts like the current Friday rules),
    'weekend_second' (acts like the current Saturday rules), or 'weekday'.
    Falls back to plain Friday/Saturday when cal has no calendar assigned.
    """
    first  = cal.weekend_first_day  if cal and cal.weekend_first_day  else 'Friday'
    second = cal.weekend_second_day if cal and cal.weekend_second_day else 'Saturday'
    if day_name == first:
        return 'weekend_first'
    if day_name == second:
        return 'weekend_second'
    return 'weekday'


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    # ─── Basic Fields ─────────────────────────────────────────────────────────

    day_name = fields.Char(
        string="Day",
        compute='_compute_day_name',
        store=True,
    )
    sunset = fields.Float(
        string="Sunset Time",   # e.g. 18.5 = 6:30 PM
    )
    attendance_date = fields.Date(
        string='Date',
        compute='_compute_attendance_date',
        store=True,
        help="Date extracted from check_out (date only, no time).",
    )
    missing_punch = fields.Selection(
        [
            ('check-in',  'Check-in'),
            ('check-out', 'Check-out'),
            ('both',      'Both'),
        ],
        string='Missing Punch',
    )

    # ─── Step 1 : Raw total overtime ──────────────────────────────────────────

    over_time_worked_hours = fields.Float(
        string='Raw Overtime Hours',
        compute='_compute_overtime',
        store=True,
        help="Total overtime hours exceeding the standard 8-hour shift (weekdays only).",
    )

    # ─── Link to Deduction record ────────────────────────────────────────────

    deduction_id = fields.Many2one(
        'deduction',
        string='Deduction Record',
        ondelete='set null',
    )

    # ─── Helper ──────────────────────────────────────────────────────────────

    def _to_local_hour(self, dt):
        """Convert a UTC datetime to a local float hour by adding UTC_OFFSET."""
        return dt.hour + dt.minute / 60.0 + UTC_OFFSET

    def _get_calendar(self, rec):
        cal = rec.employee_id.resource_calendar_id if rec.employee_id else False
        return cal

    def _effective_checkin_hour(self, rec):
        cal         = self._get_calendar(rec)
        first_ci    = cal.first_check_in if cal and cal.first_check_in else 7.0
        checkin_local = self._to_local_hour(rec.check_in)
        return max(checkin_local, first_ci)

    # ─── Compute Methods ─────────────────────────────────────────────────────

    @api.depends('check_in')
    def _compute_day_name(self):
        for rec in self:
            rec.day_name = rec.check_in.strftime('%A') if rec.check_in else False

    @api.depends('check_out')
    def _compute_attendance_date(self):
        """Extract date only from check_out (no time)."""
        for rec in self:
            rec.attendance_date = rec.check_out.date() if rec.check_out else False

    # --- Step 1 ---

    @api.depends('check_in', 'check_out', 'day_name', 'employee_id.resource_calendar_id')
    def _compute_overtime(self):
        """
        Raw overtime = worked hours - 8, for weekdays only.
        Any time worked after SHIFT_END (07:00 PM local) is ignored, even
        if the actual check-out is later — the checkout used for the
        calculation is capped at SHIFT_END.
        Weekend days (Friday / Saturday) are handled entirely in _compute_total_overtime.
        Public holidays follow the weekend-first rules there too, so they are
        excluded here as well.
        Part-time employees never get overtime — their extra hours are capped at 8.
        """
        cfg            = self.env['esri.config'].get_config()
        ot_cutoff      = cfg.overtime_cutoff or 19.0
        Overtime       = self.env['hr.overtime']

        for rec in self:
            is_part_time = rec.employee_id and \
                'part time' in (rec.employee_id.resource_calendar_id.name or '').lower()

            checkin_local = self._to_local_hour(rec.check_in) if rec.check_in else 0.0
            cal           = self._get_calendar(rec)
            hours_per_day = cal.daily_work_hours if cal and cal.daily_work_hours else STANDARD_HOURS
            last_co       = cal.last_check_out   if cal and cal.last_check_out   else 19.0

            # Public holidays are handled by the weekend-first rules in
            # hr.overtime, so no raw weekday overtime is accrued on them.
            is_holiday = bool(rec.check_out) and Overtime._is_public_holiday(rec.check_out.date())

            if not is_part_time and rec.check_in and rec.check_out and not is_holiday \
                    and classify_day(cal, rec.day_name) == 'weekday' and checkin_local <= last_co:
                checkin_effective  = self._effective_checkin_hour(rec)
                checkout_local     = self._to_local_hour(rec.check_out)
                checkout_effective = min(checkout_local, ot_cutoff)
                raw_worked = checkout_effective - checkin_effective
                rec.over_time_worked_hours = max(raw_worked - hours_per_day, 0.0)
            else:
                rec.over_time_worked_hours = 0.0

    # --- Auto-create / update Deduction record ---

    def _sync_deduction_record(self):
        # Delegate to the deduction model's central daily-record logic.
        Deduction = self.env['deduction'].sudo()
        for rec in self:
            if rec.employee_id and rec.attendance_date:
                Deduction._ensure_record(rec.employee_id.id, rec.attendance_date)

    # --- Auto-create / update Overtime record ---

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

    def _get_approved_leave_hours(self, rec):
        """Approved time-off hours covering rec.attendance_date."""
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id',       '=', rec.employee_id.id),
            ('request_date_from', '<=', rec.attendance_date),
            ('request_date_to',   '>=', rec.attendance_date),
            ('state',             '=',  'validate'),
        ])
        hours = 0.0
        for leave in leaves:
            hours += self._leave_day_hours(leave)
        return hours

    def _sync_overtime_record(self):
        # Delegate to hr.overtime's central logic so attendance sync, the
        # daily cron, and the leave-approval hook all compute identically.
        Overtime = self.env['hr.overtime'].sudo()
        for rec in self:
            if rec.employee_id and rec.attendance_date:
                Overtime._ensure_record(rec.employee_id.id, rec.attendance_date)

    def _sync_daily_report_record(self):
        DailyReport = self.env['daily.report'].sudo()
        for rec in self:
            if rec.employee_id and rec.attendance_date:
                DailyReport._ensure_record(rec.employee_id.id, rec.attendance_date)

    def _do_sync(self):
        self.env.flush_all()
        self.invalidate_recordset()
        self._sync_deduction_record()
        self._sync_overtime_record()
        self._sync_daily_report_record()

    def write(self, vals):
        if self.env.context.get('_esri_syncing'):
            return super().write(vals)
        # Guard: if only deduction_id is being set, skip sync to avoid recursion.
        if set(vals.keys()) == {"deduction_id"}:
            return super().write(vals)
        res = super().write(vals)
        self.with_context(_esri_syncing=True)._do_sync()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.with_context(_esri_syncing=True)._do_sync()
        return records