from datetime import date, datetime, time, timedelta
from odoo import fields, models, api
from odoo.exceptions import UserError
from .models import classify_day

WEEKDAYS = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Sunday'}


class Overtime(models.Model):
    _name        = 'hr.overtime'
    _description = 'Employee Daily Overtime'
    _inherit     = ['mail.thread', 'mail.activity.mixin']
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id    = fields.Many2one('hr.employee', string='Employee', required=True)
    date           = fields.Date(string='Date', required=True)
    overtime_hours = fields.Float(string='Raw Overtime Hours', digits=(16, 2))
    employee_rule  = fields.Selection(
        related='employee_id.employee_rule',
        string='Employee Rule',
        store=True,
        readonly=True,
    )

    state = fields.Selection(
        [
            ('draft',    'Draft'),
            ('posted',   'Posted'),
            ('archived', 'Archived'),
        ],
        string='Status',
        default='draft',
        required=True,
    )

    overtime_hours_before_sunset = fields.Float(
        string='Overtime Hours Before Sunset',
        compute='_compute_overtime_split',
        store=True,
    )
    overtime_hours_after_sunset = fields.Float(
        string='Overtime Hours After Sunset',
        compute='_compute_overtime_split',
        store=True,
    )
    weighted_overtime_before_sunset = fields.Float(
        string='Weighted Overtime Before Sunset',
        compute='_compute_weighted_overtime',
        store=True,
    )
    weighted_overtime_after_sunset = fields.Float(
        string='Weighted Overtime After Sunset',
        compute='_compute_weighted_overtime',
        store=True,
    )
    total_overtime_hours = fields.Float(
        string='Total Weighted Overtime',
        compute='_compute_total_overtime',
        store=True,
    )
    all_worked_hours = fields.Float(
        string='All Worked Hours',
        compute='_compute_all_worked_hours',
        store=True,
    )
    overtime_start_time = fields.Float(
        string='Overtime Start Time',
        compute='_compute_overtime_start_time',
        store=True,
        help="الوقت اللى بيبدأ منه احتساب الـ overtime فى اليوم ده: "
             "وقت الحضور الفعلى + (ساعات اليوم الكامل - ساعات الإجازة المعتمدة).",
    )
    worked_overtime = fields.Float(
        string='Worked Overtime',
        compute='_compute_worked_overtime',
        store=True,
        help="الوقت ما بين Overtime Start Time والـ Effective Check-Out "
             "(آخر انصراف بحد أقصى الـ Overtime Cutoff).",
    )

    def _get_attendance(self, rec):
        return self.env['hr.attendance'].sudo().search([
            ('employee_id',     '=', rec.employee_id.id),
            ('attendance_date', '=', rec.date),
        ], limit=1)

    def _is_office_boy(self, rec):
        return (rec.employee_id.employee_rule or '') == 'office_boy'

    @api.depends('employee_id', 'date', 'overtime_hours',
                 'overtime_start_time', 'worked_overtime')
    def _compute_overtime_split(self):
        UTC_OFFSET = 3.0
        for rec in self:
            # Weekend-first/second days use a flat rate — no before/after
            # sunset split. Exception: office boy on the weekend-second day
            # who worked MORE than a full day — all his worked hours are
            # split before/after sunset.
            cal      = rec.employee_id.resource_calendar_id if rec.employee_id else False
            day_kind = classify_day(cal, rec.date.strftime('%A')) if rec.date else 'weekday'
            if rec.date and self._is_public_holiday(rec.date):
                day_kind = 'weekend_first'   # public holidays follow the weekend-first rules
            skip_day       = day_kind in ('weekend_first', 'weekend_second')
            office_boy_sat = False
            if skip_day and day_kind == 'weekend_second' and rec.employee_id and self._is_office_boy(rec):
                hpd = cal.daily_work_hours if cal and cal.daily_work_hours else 8.0
                wh  = self._get_actual_worked_hours_for(rec.employee_id, rec.date)
                if wh > hpd:
                    skip_day       = False
                    office_boy_sat = True
            if skip_day or not rec.employee_id or not rec.date:
                rec.overtime_hours_before_sunset = 0.0
                rec.overtime_hours_after_sunset  = 0.0
                continue

            cal    = rec.employee_id.resource_calendar_id
            sunset = cal.sunset if cal and cal.sunset else 18.0

            if office_boy_sat:
                # All worked hours split by the actual check-out time
                att      = self._get_attendance(rec)
                total_ot = rec.overtime_hours
                if att and att.check_out and total_ot:
                    checkout_local = att.check_out.hour + att.check_out.minute / 60.0 + UTC_OFFSET
                    if checkout_local > sunset:
                        after  = min(checkout_local - sunset, total_ot)
                        before = total_ot - after
                    else:
                        before = total_ot
                        after  = 0.0
                else:
                    before = total_ot or 0.0
                    after  = 0.0
            else:
                # Weekdays: before = hours between overtime start and sunset,
                # after = the rest of the worked overtime.
                start  = rec.overtime_start_time
                worked = rec.worked_overtime
                if not start or not worked:
                    rec.overtime_hours_before_sunset = 0.0
                    rec.overtime_hours_after_sunset  = 0.0
                    continue
                before = min(max(sunset - start, 0.0), worked)
                after  = worked - before

            rec.overtime_hours_before_sunset = max(before, 0.0)
            rec.overtime_hours_after_sunset  = max(after,  0.0)

    @api.depends('overtime_hours_before_sunset', 'overtime_hours_after_sunset')
    def _compute_weighted_overtime(self):
        for rec in self:
            cfg          = self.env['esri.config'].get_config()
            rate_before  = cfg.overtime_rate_before_sunset or 1.35
            rate_after   = cfg.overtime_rate_after_sunset  or 1.70
            rec.weighted_overtime_before_sunset = rec.overtime_hours_before_sunset * rate_before
            rec.weighted_overtime_after_sunset  = rec.overtime_hours_after_sunset  * rate_after

    @api.depends('weighted_overtime_before_sunset', 'weighted_overtime_after_sunset',
                 'employee_id', 'date', 'overtime_hours')
    def _compute_total_overtime(self):
        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.total_overtime_hours = 0.0
                continue

            att      = self._get_attendance(rec)
            cal      = rec.employee_id.resource_calendar_id
            hpd      = cal.daily_work_hours if cal and cal.daily_work_hours else 8.0
            cfg      = self.env['esri.config'].get_config()

            day_kind = classify_day(cal, rec.date.strftime('%A'))
            # Public holidays follow the weekend-first rules for everyone
            if self._is_public_holiday(rec.date):
                day_kind = 'weekend_first'

            if day_kind == 'weekday':
                rec.total_overtime_hours = (
                    rec.weighted_overtime_before_sunset + rec.weighted_overtime_after_sunset
                )
            elif day_kind == 'weekend_first':
                worked = self._get_actual_worked_hours_for(rec.employee_id, rec.date)
                total  = (cfg.friday_rate or 2.0) * hpd if worked > 0 else 0.0
                if self._is_office_boy(rec) and total:
                    # Office boy: deduct the hours he actually worked
                    total = max(total - worked, 0.0)
                rec.total_overtime_hours = total
            elif day_kind == 'weekend_second':
                # Weekend overtime is based on ACTUAL worked hours
                wh = self._get_actual_worked_hours_for(rec.employee_id, rec.date)
                saturday_first  = cfg.saturday_first_rate  or 0.5
                saturday_second = cfg.saturday_second_rate or 1.0
                if self._is_office_boy(rec):
                    if wh > hpd:
                        # All worked hours weighted before/after sunset
                        rec.total_overtime_hours = (
                            rec.weighted_overtime_before_sunset + rec.weighted_overtime_after_sunset
                        )
                    else:
                        base = 0.0
                        if 0 < wh <= hpd / 2.0:
                            base = saturday_first  * hpd
                        elif hpd / 2.0 < wh <= hpd:
                            base = saturday_second * hpd
                        rec.total_overtime_hours = max(base - wh, 0.0)
                elif 0 < wh <= hpd / 2.0:
                    rec.total_overtime_hours = saturday_first  * hpd
                elif hpd / 2.0 < wh <= hpd:
                    rec.total_overtime_hours = saturday_second * hpd
                else:
                    rec.total_overtime_hours = 0.0
            else:
                rec.total_overtime_hours = 0.0

    @api.depends('employee_id', 'date')
    def _compute_all_worked_hours(self):
        Attendance = self.env['hr.attendance'].sudo()
        Leave      = self.env['hr.leave'].sudo()
        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.all_worked_hours = 0.0
                continue

            att = Attendance.search([
                ('employee_id',     '=', rec.employee_id.id),
                ('attendance_date', '=', rec.date),
            ], limit=1)
            worked = att.worked_hours if att else 0.0

            rec.all_worked_hours = worked + self._get_approved_time_off(rec)

    def _get_approved_time_off(self, rec):
        """Approved time-off hours covering rec.date for rec.employee_id."""
        return self._get_approved_time_off_for(rec.employee_id, rec.date)

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
    def _get_approved_time_off_for(self, employee, target_date):
        """Approved time-off hours covering target_date for the employee."""
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id',       '=', employee.id),
            ('request_date_from', '<=', target_date),
            ('request_date_to',   '>=', target_date),
            ('state',             '=',  'validate'),
        ])
        time_off = 0.0
        for leave in leaves:
            time_off += self._leave_day_hours(leave)
        return time_off

    @api.model
    def _is_public_holiday(self, target_date):
        """True when target_date falls inside a company public holiday
        (a resource.calendar.leaves entry with no specific resource)."""
        day_start = datetime.combine(target_date, time.min)
        day_end   = datetime.combine(target_date, time.max)
        return bool(self.env['resource.calendar.leaves'].sudo().search_count([
            ('resource_id', '=', False),
            ('date_from',   '<=', fields.Datetime.to_string(day_end)),
            ('date_to',     '>=', fields.Datetime.to_string(day_start)),
        ]))

    @api.model
    def _get_actual_worked_hours_for(self, employee, target_date):
        """
        Actual worked hours, same formula as the daily report:
        (worked hours + approved time off) minus the time beyond the
        overtime cutoff, capped at the configured max worked hours.
        """
        UTC_OFFSET = 3.0
        cfg       = self.env['esri.config'].get_config()
        ot_cutoff = cfg.overtime_cutoff or 19.0
        max_wh    = cfg.max_worked_hours or 12.0

        cal      = employee.resource_calendar_id
        first_ci = cal.first_check_in if cal and cal.first_check_in else 7.0

        atts = self.env['hr.attendance'].sudo().search([
            ('employee_id',     '=', employee.id),
            ('attendance_date', '=', target_date),
        ])
        worked   = 0.0
        last_out = 0.0
        for att in atts:
            if not att.check_in or not att.check_out:
                continue
            checkin_local  = att.check_in.hour  + att.check_in.minute  / 60.0 + UTC_OFFSET
            checkout_local = att.check_out.hour + att.check_out.minute / 60.0 + UTC_OFFSET
            checkin_effective = max(checkin_local, first_ci)
            worked += max(checkout_local - checkin_effective, 0.0)
            last_out = max(last_out, checkout_local)

        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id',       '=', employee.id),
            ('request_date_from', '<=', target_date),
            ('request_date_to',   '>=', target_date),
            ('state',             '=',  'validate'),
        ])
        time_off = 0.0
        for leave in leaves:
            time_off += self._leave_day_hours(leave)
            if getattr(leave, 'request_unit_hours', False):
                hour_to = getattr(leave, 'request_hour_to', False)
                try:
                    hour_to = float(hour_to) if hour_to else 0.0
                except (TypeError, ValueError):
                    hour_to = 0.0
                last_out = max(last_out, hour_to)

        total = worked + time_off
        minus = max(last_out - ot_cutoff, 0.0)
        return min(max(total - minus, 0.0), max_wh)

    @api.depends('employee_id', 'date', 'overtime_hours')
    def _compute_overtime_start_time(self):
        """
        Walk the day's timeline (attendance intervals + approved time-off
        intervals, in chronological order) and find the exact local hour at
        which the employee completes the full daily hours (hpd). That moment
        is when overtime starts counting.

        Example: work 9:00-13:00 (4h) then approved business mission
        13:00-20:00 -> the 8th hour completes at 17:00 -> overtime starts 17:00.
        """
        UTC_OFFSET = 3.0
        Attendance = self.env['hr.attendance'].sudo()
        Leave      = self.env['hr.leave'].sudo()

        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.overtime_start_time = 0.0
                continue

            # Staff employees never accrue overtime on regular weekdays.
            cal      = rec.employee_id.resource_calendar_id
            day_kind = classify_day(cal, rec.date.strftime('%A'))
            if (rec.employee_id.employee_rule or '') == 'staff' and day_kind == 'weekday':
                rec.overtime_start_time = 0.0
                continue

            hpd      = cal.daily_work_hours if cal and cal.daily_work_hours else 8.0
            first_ci = cal.first_check_in   if cal and cal.first_check_in   else 7.0
            last_co  = cal.last_check_out   if cal and cal.last_check_out   else 19.0

            segments = []

            # ── Attendance intervals (local hours) ──────────────────────────
            atts = Attendance.search([
                ('employee_id',     '=', rec.employee_id.id),
                ('attendance_date', '=', rec.date),
            ])
            for att in atts:
                if not att.check_in:
                    continue
                start = max(att.check_in.hour + att.check_in.minute / 60.0 + UTC_OFFSET, first_ci)
                if att.check_out:
                    end = att.check_out.hour + att.check_out.minute / 60.0 + UTC_OFFSET
                else:
                    end = 24.0   # still checked in — open until end of day
                if end > start:
                    segments.append((start, end))

            # ── Approved time-off intervals ─────────────────────────────────
            leaves = Leave.search([
                ('employee_id',       '=', rec.employee_id.id),
                ('request_date_from', '<=', rec.date),
                ('request_date_to',   '>=', rec.date),
                ('state',             '=',  'validate'),
            ])
            for leave in leaves:
                hour_from = getattr(leave, 'request_hour_from', False)
                hour_to   = getattr(leave, 'request_hour_to',   False)
                unit_hours = getattr(leave, 'request_unit_hours', False)
                start = end = 0.0
                if unit_hours and hour_from and hour_to:
                    try:
                        start, end = float(hour_from), float(hour_to)
                    except (TypeError, ValueError):
                        start = end = 0.0
                if end <= start:
                    # No explicit hours on the leave: append its daily share
                    # right after the last known segment.
                    hours = self._leave_day_hours(leave)
                    if hours <= 0:
                        continue
                    anchor = max(s[1] for s in segments) if segments else first_ci
                    start, end = anchor, anchor + hours
                # Clip the leave to the working-day window
                start = max(start, first_ci)
                end   = min(end,   last_co)
                if end > start:
                    segments.append((start, end))

            if not segments:
                rec.overtime_start_time = 0.0
                continue

            # ── Walk the timeline until hpd hours are completed ─────────────
            segments.sort()
            accumulated = 0.0
            result      = 0.0
            for start, end in segments:
                duration = end - start
                if accumulated + duration >= hpd:
                    result = start + (hpd - accumulated)
                    break
                accumulated += duration

            rec.overtime_start_time = result

    @api.depends('overtime_start_time')
    def _compute_worked_overtime(self):
        UTC_OFFSET = 3.0
        Attendance = self.env['hr.attendance'].sudo()
        Leave      = self.env['hr.leave'].sudo()

        for rec in self:
            if not rec.employee_id or not rec.date or not rec.overtime_start_time:
                rec.worked_overtime = 0.0
                continue

            cfg       = self.env['esri.config'].get_config()
            ot_cutoff = cfg.overtime_cutoff or 19.0

            # Last physical check-out (local hour)
            last_out = 0.0
            atts = Attendance.search([
                ('employee_id',     '=', rec.employee_id.id),
                ('attendance_date', '=', rec.date),
            ])
            for att in atts:
                if att.check_out:
                    out_local = att.check_out.hour + att.check_out.minute / 60.0 + UTC_OFFSET
                    last_out = max(last_out, out_local)

            # End ('to') of hour-based approved leaves
            leaves = Leave.search([
                ('employee_id',       '=', rec.employee_id.id),
                ('request_date_from', '<=', rec.date),
                ('request_date_to',   '>=', rec.date),
                ('state',             '=',  'validate'),
            ])
            for leave in leaves:
                if getattr(leave, 'request_unit_hours', False):
                    hour_to = getattr(leave, 'request_hour_to', False)
                    try:
                        hour_to = float(hour_to) if hour_to else 0.0
                    except (TypeError, ValueError):
                        hour_to = 0.0
                    last_out = max(last_out, hour_to)

            effective_out = min(last_out, ot_cutoff) if last_out else 0.0
            rec.worked_overtime = max(effective_out - rec.overtime_start_time, 0.0)

    _sql_constraints = [
        ('unique_employee_date', 'UNIQUE(employee_id, date)',
         'An overtime record already exists for this employee on this date.'),
    ]

    # ─── Block manual create / write / unlink ────────────────────────────────

    def _is_internal_call(self):
        return self.env.context.get('overtime_sync') or self.env.su

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_internal_call():
            raise UserError("Overtime records are created automatically from attendance. You cannot create them manually.")
        return super().create(vals_list)

    def write(self, vals):
        if not self._is_internal_call():
            raise UserError("Overtime records are updated automatically from attendance. You cannot edit them manually.")
        return super().write(vals)

    def unlink(self):
        return super().unlink()

    # ─── Status bar transitions ────────────────────────────────────────────────

    def action_post(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError("Only draft records can be posted.")
        self.with_context(overtime_sync=True).write({'state': 'posted'})

    def action_set_to_draft(self):
        self.with_context(overtime_sync=True).write({'state': 'draft'})

    def action_archive_record(self):
        self.with_context(overtime_sync=True).write({'state': 'archived'})

    # ─── Cron ────────────────────────────────────────────────────────────────

    @api.model
    def _cron_create_daily_overtime(self):
        self._create_overtime_for_date(date.today() - timedelta(days=1))

    _DAY_MAP = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
                'Friday': 4, 'Saturday': 5, 'Sunday': 6}

    @api.model
    def _is_stuff_fullday(self, emp, day_name):
        """Return True if employee is stuff AND their schedule's total hours for day_name
        equals or exceeds daily_work_hours (full-day schedule for that weekday)."""
        if (emp.employee_rule or '') != 'staff':
            return False
        cal = emp.resource_calendar_id
        if not cal:
            return False
        dow = self._DAY_MAP.get(day_name)
        if dow is None:
            return False
        day_lines   = cal.attendance_ids.filtered(lambda l: int(l.dayofweek) == dow)
        sched_hours = sum(l.hour_to - l.hour_from for l in day_lines)
        hpd         = cal.daily_work_hours or 8.0
        return sched_hours >= hpd

    @api.model
    def _compute_overtime_hours_for_employee(self, emp, target_date):
        """Raw overtime hours for one employee on one date (no record write)."""
        Attendance = self.env['hr.attendance'].sudo()
        att = Attendance.search([
            ('employee_id',     '=', emp.id),
            ('attendance_date', '=', target_date),
        ], limit=1)

        cal           = emp.resource_calendar_id
        hpd           = cal.daily_work_hours if cal and cal.daily_work_hours else 8.0
        is_office_boy = (emp.employee_rule or '') == 'office_boy'
        is_staff      = (emp.employee_rule or '') == 'staff'

        day_kind = classify_day(cal, target_date.strftime('%A'))
        # Public holidays follow the weekend-first rules for everyone
        if self._is_public_holiday(target_date):
            day_kind = 'weekend_first'

        overtime = 0.0

        # Staff employee → no overtime on regular weekdays at all.
        # The weekend-first/second days always follow the normal weekend rules.
        staff_no_overtime = is_staff and day_kind == 'weekday'

        if not staff_no_overtime:
            if day_kind == 'weekday':
                if att:
                    overtime = att.over_time_worked_hours or 0.0
            elif day_kind in ('weekend_first', 'weekend_second'):
                # Weekend overtime is based on ACTUAL worked hours
                # (worked + approved time off - beyond-cutoff time, capped)
                wh = self._get_actual_worked_hours_for(emp, target_date)
                if day_kind == 'weekend_first':
                    if wh > 0:
                        overtime = hpd
                else:
                    if is_office_boy:
                        if wh > hpd:
                            overtime = wh
                        else:
                            base = 0.0
                            if 0 < wh <= hpd / 2.0:
                                base = hpd / 2.0
                            elif hpd / 2.0 < wh <= hpd:
                                base = hpd
                            overtime = max(base - wh, 0.0)
                    else:
                        if 0 < wh <= hpd / 2.0:
                            overtime = hpd / 2.0
                        elif hpd / 2.0 < wh <= hpd:
                            overtime = hpd

        return overtime

    @api.model
    def _ensure_record(self, employee_id, target_date):
        """Create/update the (employee, date) overtime record with a fresh value."""
        OT = self.with_context(overtime_sync=True)

        existing = OT.search([
            ('employee_id', '=', employee_id),
            ('date',        '=', target_date),
        ], limit=1)

        emp      = self.env['hr.employee'].sudo().browse(employee_id)
        overtime = self._compute_overtime_hours_for_employee(emp, target_date)

        if existing:
            existing.write({'overtime_hours': overtime})
        else:
            existing = OT.create({
                'employee_id':    employee_id,
                'date':           target_date,
                'overtime_hours': overtime,
            })
        return existing

    @api.model
    def _create_overtime_for_date(self, target_date):
        employees = self.env['hr.employee'].sudo().search([('active', '=', True)])
        for emp in employees:
            self._ensure_record(emp.id, target_date)
