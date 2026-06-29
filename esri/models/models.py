from odoo import models, fields, api

# ─── Constants ───────────────────────────────────────────────────────────────

WEEKDAYS       = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Sunday'}
STANDARD_HOURS = 8.0
UTC_OFFSET     = 3.0

RATE_BEFORE_SUNSET   = 1.35   # overtime multiplier before sunset
RATE_AFTER_SUNSET    = 1.70   # overtime multiplier after sunset

LATE_CHECKIN_LIMIT   = 11.0   # deduct if check-in is after  11:00 AM (local)
LATE_GRACE_MINUTES   = 15     # first late occurrence ≤ this many minutes -> free once/month
LATE_HALF_DAY_LIMIT  = 60     # up to 60 min late -> half-day deduction
QUARTER_DAY_HOURS    = 2.0    # hours deducted for second ≤15-min late in same month
HALF_DAY_HOURS       = 4.0    # hours deducted for 16-60 min late
EARLY_CHECKOUT_LIMIT = 15.0   # deduct if check-out is before 03:00 PM (local)
SHIFT_START          = 7.0    # ignore any check-in before 07:00 AM (local)
SHIFT_END            = 19.0   # ignore attendance if check-in is after 07:00 PM (local)


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

    # ─── Step 1 : Raw total overtime ──────────────────────────────────────────

    over_time_worked_hours = fields.Float(
        string='Raw Overtime Hours',
        compute='_compute_overtime',
        store=True,
        help="Total overtime hours exceeding the standard 8-hour shift (weekdays only).",
    )

    # ─── Step 2 : Raw overtime split before / after sunset ───────────────────

    overtime_hours_before_sunset = fields.Float(
        string='Overtime Hours Before Sunset',
        compute='_compute_overtime_hours_before_sunset',
        store=True,
        help="Raw overtime hours that fall before sunset.",
    )
    overtime_hours_after_sunset = fields.Float(
        string='Overtime Hours After Sunset',
        compute='_compute_overtime_hours_after_sunset',
        store=True,
        help="Raw overtime hours that fall after sunset.",
    )

    # ─── Step 3 : Weighted overtime (hours x rate) ───────────────────────────

    weighted_overtime_before_sunset = fields.Float(
        string='Weighted Overtime Before Sunset (x 1.35)',
        compute='_compute_weighted_overtime_before_sunset',
        store=True,
        help="Overtime hours before sunset multiplied by rate 1.35.",
    )
    weighted_overtime_after_sunset = fields.Float(
        string='Weighted Overtime After Sunset (x 1.70)',
        compute='_compute_weighted_overtime_after_sunset',
        store=True,
        help="Overtime hours after sunset multiplied by rate 1.70.",
    )

    # ─── Step 4 : Grand total ─────────────────────────────────────────────────

    total_overtime_hours = fields.Float(
        string='Total Weighted Overtime',
        compute='_compute_total_overtime',
        store=True,
        help="Sum of weighted overtime before and after sunset (weekdays), "
             "or special rule for Friday / Saturday.",
    )

    # ─── Link to Deduction record ────────────────────────────────────────────

    deduction_id = fields.Many2one(
        'deduction',
        string='Deduction Record',
        ondelete='set null',
    )

    # ─── Deduction ────────────────────────────────────────────────────────────

    deduction_hours = fields.Float(
        string='Deduction Hours',
        compute='_compute_deduction_hours',
        store=True,
        help=(
            "Late arrival   : check-in after 11:00 AM  -> deduct (check_in - 11:00).\n"
            "Early departure: check-out before 03:00 PM -> deduct (15:00 - check_out).\n"
            "Short shift    : worked < 8 hours          -> deduct (8 - worked_hours).\n"
            "Final deduction = max(late + early, short_shift) to avoid double-counting."
        ),
    )

    # ─── Helper ──────────────────────────────────────────────────────────────

    def _to_local_hour(self, dt):
        """Convert a UTC datetime to a local float hour by adding UTC_OFFSET."""
        return dt.hour + dt.minute / 60.0 + UTC_OFFSET

    def _effective_checkin_hour(self, rec):
        """
        Return the effective local check-in hour used in all calculations.
        If the employee arrives before SHIFT_START (07:00 AM),
        treat it as SHIFT_START to ignore early hours.
        """
        checkin_local = self._to_local_hour(rec.check_in)
        return max(checkin_local, SHIFT_START)

    def _grace_used_this_month(self, rec):
        """
        Return the number of times this employee was late by ≤15 min
        on days BEFORE rec.attendance_date in the same calendar month.
        Used to decide whether the free-once-a-month grace applies.
        """
        if not rec.employee_id or not rec.attendance_date:
            return 0
        month_start = rec.attendance_date.replace(day=1)
        earlier = self.env['hr.attendance'].search([
            ('employee_id',     '=', rec.employee_id.id),
            ('attendance_date', '>=', month_start),
            ('attendance_date', '<',  rec.attendance_date),
            ('id',              '!=', rec.id),
        ])
        count = 0
        for att in earlier:
            if att.check_in:
                ci_local = self._to_local_hour(att.check_in)
                ci_eff   = max(ci_local, SHIFT_START)
                lm = (ci_eff - LATE_CHECKIN_LIMIT) * 60 if ci_eff > LATE_CHECKIN_LIMIT else 0.0
                if 0 < lm <= LATE_GRACE_MINUTES:
                    count += 1
        return count

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

    @api.depends('check_in', 'check_out', 'day_name')
    def _compute_overtime(self):
        """
        Raw overtime = worked hours - 8, for weekdays only.
        Any time worked after SHIFT_END (07:00 PM local) is ignored, even
        if the actual check-out is later — the checkout used for the
        calculation is capped at SHIFT_END.
        Weekend days (Friday / Saturday) are handled entirely in _compute_total_overtime.
        """
        for rec in self:
            checkin_local = self._to_local_hour(rec.check_in) if rec.check_in else 0.0
            if rec.check_in and rec.check_out and rec.day_name in WEEKDAYS \
                    and checkin_local <= SHIFT_END:
                checkin_effective  = self._effective_checkin_hour(rec)
                checkout_local     = self._to_local_hour(rec.check_out)
                checkout_effective = min(checkout_local, SHIFT_END)
                raw_worked = checkout_effective - checkin_effective
                rec.over_time_worked_hours = max(raw_worked - STANDARD_HOURS, 0.0)
            else:
                rec.over_time_worked_hours = 0.0

    # --- Step 2a ---

    @api.depends('day_name', 'over_time_worked_hours', 'sunset', 'check_out')
    def _compute_overtime_hours_before_sunset(self):
        """Raw overtime hours that fall BEFORE sunset."""
        for rec in self:
            if rec.day_name not in WEEKDAYS:
                rec.overtime_hours_before_sunset = 0.0
                continue

            total_ot = rec.over_time_worked_hours
            if not (rec.check_out and rec.sunset and total_ot > 0):
                rec.overtime_hours_before_sunset = 0.0
                continue

            checkout_local = min(self._to_local_hour(rec.check_out), SHIFT_END)

            if checkout_local > rec.sunset:
                after  = min(checkout_local - rec.sunset, total_ot)
                before = total_ot - after
            else:
                before = total_ot

            rec.overtime_hours_before_sunset = max(before, 0.0)

    # --- Step 2b ---

    @api.depends('day_name', 'over_time_worked_hours', 'sunset', 'check_out')
    def _compute_overtime_hours_after_sunset(self):
        """Raw overtime hours that fall AFTER sunset."""
        for rec in self:
            if rec.day_name not in WEEKDAYS:
                rec.overtime_hours_after_sunset = 0.0
                continue

            total_ot = rec.over_time_worked_hours
            if not (rec.check_out and rec.sunset and total_ot > 0):
                rec.overtime_hours_after_sunset = 0.0
                continue

            checkout_local = min(self._to_local_hour(rec.check_out), SHIFT_END)

            if checkout_local > rec.sunset:
                after = min(checkout_local - rec.sunset, total_ot)
            else:
                after = 0.0

            rec.overtime_hours_after_sunset = max(after, 0.0)

    # --- Step 3a ---

    @api.depends('overtime_hours_before_sunset')
    def _compute_weighted_overtime_before_sunset(self):
        """Weighted overtime before sunset = raw hours before sunset x 1.35"""
        for rec in self:
            rec.weighted_overtime_before_sunset = (
                rec.overtime_hours_before_sunset * RATE_BEFORE_SUNSET
            )

    # --- Step 3b ---

    @api.depends('overtime_hours_after_sunset')
    def _compute_weighted_overtime_after_sunset(self):
        """Weighted overtime after sunset = raw hours after sunset x 1.70"""
        for rec in self:
            rec.weighted_overtime_after_sunset = (
                rec.overtime_hours_after_sunset * RATE_AFTER_SUNSET
            )

    # --- Step 4 ---

    @api.depends(
        'weighted_overtime_before_sunset',
        'weighted_overtime_after_sunset',
        'day_name',
        'worked_hours',
    )
    def _compute_total_overtime(self):
        """
        Grand total weighted overtime, rules by day type:

        Weekdays (Mon / Tue / Wed / Thu / Sun):
            total = weighted_before + weighted_after

        Friday (day off):
            worked_hours > 0  ->  total = 16 (flat)
            otherwise         ->  total = 0

        Saturday (day off):
            worked_hours > 8  ->  total = 0
            1 <= wh <= 3      ->  total = 4
            4 <= wh <= 8      ->  total = 8
            otherwise         ->  total = 0
        """
        for rec in self:
            day = rec.day_name

            if day in WEEKDAYS:
                rec.total_overtime_hours = (
                    rec.weighted_overtime_before_sunset
                    + rec.weighted_overtime_after_sunset
                )

            elif day == 'Friday':
                # Any hours worked on Friday count as a flat 16 hours.
                rec.total_overtime_hours = 16.0 if rec.worked_hours > 0 else 0.0

            elif day == 'Saturday':
                wh = rec.worked_hours
                if wh > STANDARD_HOURS:
                    rec.total_overtime_hours = 0.0
                elif 1 <= wh <= 3:
                    rec.total_overtime_hours = 4.0
                elif 4 <= wh <= 8:
                    rec.total_overtime_hours = STANDARD_HOURS
                else:
                    rec.total_overtime_hours = 0.0

            else:
                rec.total_overtime_hours = 0.0

    # --- Deduction ---

    @api.depends('check_in', 'check_out', 'worked_hours', 'day_name')
    def _compute_deduction_hours(self):
        """
        Calculate deduction hours based on three rules (weekdays only):

        1. Late arrival   : check-in  after  11:00 AM -> deduct (check_in  - 11:00)
        2. Early departure: check-out before 03:00 PM -> deduct (15:00 - check_out)
        3. Short shift    : worked_hours < 8          -> deduct (8 - worked_hours)

        Final deduction = max(rule1 + rule2, rule3)
        """
        for rec in self:
            if not rec.check_in or rec.day_name not in WEEKDAYS:
                rec.deduction_hours = 0.0
                continue

            if not rec.check_out:
                rec.deduction_hours = 0.0
                continue

            if self._to_local_hour(rec.check_in) > SHIFT_END:
                rec.deduction_hours = 0.0
                continue

            checkin_local  = self._effective_checkin_hour(rec)
            checkout_local = self._to_local_hour(rec.check_out)

            # Rule 1 — late arrival (tiered, grace once per month)
            # 0 < late ≤ 15 min, first time this month  → no deduction
            # 0 < late ≤ 15 min, second+ time this month → quarter day (2 h)
            # 16–60 min late                             → half day (4 h)
            # > 60 min late                              → full day (8 h)
            late_minutes = (checkin_local - LATE_CHECKIN_LIMIT) * 60 if checkin_local > LATE_CHECKIN_LIMIT else 0.0
            grace_free = False
            if late_minutes <= 0:
                late_arrival = 0.0
            elif late_minutes <= LATE_GRACE_MINUTES:
                if self._grace_used_this_month(rec) == 0:
                    late_arrival = 0.0
                    grace_free   = True   # first time: no penalty, adjust short_shift too
                else:
                    late_arrival = QUARTER_DAY_HOURS
            elif late_minutes <= LATE_HALF_DAY_LIMIT:
                late_arrival = HALF_DAY_HOURS
            else:
                late_arrival = STANDARD_HOURS

            # Rule 2 — early departure (strictly before 03:00 PM)
            early_departure = EARLY_CHECKOUT_LIMIT - checkout_local if checkout_local < EARLY_CHECKOUT_LIMIT else 0.0

            # Rule 3 — short shift (less than 8 hours worked)
            # When grace is free, add back the late minutes so they don't
            # cause a phantom short-shift deduction.
            adjusted_worked = rec.worked_hours + (late_minutes / 60.0 if grace_free else 0.0)
            short_shift = max(STANDARD_HOURS - adjusted_worked, 0.0)

            rec.deduction_hours = max(late_arrival + early_departure, short_shift)

    # --- Auto-create / update Deduction record ---

    def _sync_deduction_record(self):
        """
        Create or update a single deduction record per (employee, date).
        The type, day_overtime, and day_deduction are computed fields on
        the deduction model itself, so we only need to ensure a record exists.
        """
        Deduction = self.env['deduction']
        for rec in self:
            if not rec.employee_id or not rec.attendance_date:
                continue

            if not (rec.total_overtime_hours > 0 or rec.deduction_hours > 0):
                continue

            existing = Deduction.search([
                ('name', '=', rec.employee_id.id),
                ('date', '=', rec.attendance_date),
            ], limit=1)

            vals = {
                'name': rec.employee_id.id,
                'date': rec.attendance_date,
            }

            if existing:
                existing.write(vals)
                deduction_id = existing.id
            else:
                new_deduction = Deduction.create(vals)
                deduction_id = new_deduction.id

            # Use super().write() directly to avoid triggering our custom
            # write() again and causing infinite recursion.
            super(HrAttendance, rec).write({"deduction_id": deduction_id})

    def write(self, vals):
        # Guard: if only deduction_id is being set, skip sync to avoid recursion.
        if set(vals.keys()) == {"deduction_id"}:
            return super().write(vals)
        res = super().write(vals)
        self._sync_deduction_record()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_deduction_record()
        return records