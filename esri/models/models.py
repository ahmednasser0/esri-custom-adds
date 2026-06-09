from odoo import models, fields, api

# ─── Constants ───────────────────────────────────────────────────────────────

WEEKDAYS       = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Sunday'}
STANDARD_HOURS = 8.0
UTC_OFFSET     = 3.0

RATE_BEFORE_SUNSET   = 1.35   # overtime multiplier before sunset
RATE_AFTER_SUNSET    = 1.70   # overtime multiplier after sunset

LATE_CHECKIN_LIMIT   = 11.0   # deduct if check-in is after  11:00 AM (local)
EARLY_CHECKOUT_LIMIT = 15.0   # deduct if check-out is before 03:00 PM (local)


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

    # ─── Compute Methods ─────────────────────────────────────────────────────

    @api.depends('check_in')
    def _compute_day_name(self):
        for rec in self:
            rec.day_name = rec.check_in.strftime('%A') if rec.check_in else False

    # --- Step 1 ---

    @api.depends('check_in', 'check_out', 'day_name')
    def _compute_overtime(self):
        """
        Raw overtime = worked hours - 8, for weekdays only.
        Weekend days (Friday / Saturday) are handled entirely in _compute_total_overtime.
        """
        for rec in self:
            if rec.check_in and rec.check_out and rec.day_name in WEEKDAYS:
                raw_worked = (
                    rec.check_out.hour + rec.check_out.minute / 60.0
                    - rec.check_in.hour - rec.check_in.minute / 60.0
                )
                rec.over_time_worked_hours = max(raw_worked - STANDARD_HOURS, 0.0)
            else:
                rec.over_time_worked_hours = 0.0

    # --- Step 2a ---

    @api.depends('day_name', 'over_time_worked_hours', 'sunset', 'check_out')
    def _compute_overtime_hours_before_sunset(self):
        """
        Raw overtime hours that fall BEFORE sunset.

        Formula:
            checkout_local = check_out (UTC) + UTC_OFFSET
            if checkout_local > sunset:
                after  = min(checkout_local - sunset, total_ot)
                before = total_ot - after
            else:
                before = total_ot   (all overtime is before sunset)
        """
        for rec in self:
            if rec.day_name not in WEEKDAYS:
                rec.overtime_hours_before_sunset = 0.0
                continue

            total_ot = rec.over_time_worked_hours
            if not (rec.check_out and rec.sunset and total_ot > 0):
                rec.overtime_hours_before_sunset = 0.0
                continue

            checkout_local = self._to_local_hour(rec.check_out)

            if checkout_local > rec.sunset:
                after  = min(checkout_local - rec.sunset, total_ot)
                before = total_ot - after
            else:
                before = total_ot

            rec.overtime_hours_before_sunset = max(before, 0.0)

    # --- Step 2b ---

    @api.depends('day_name', 'over_time_worked_hours', 'sunset', 'check_out')
    def _compute_overtime_hours_after_sunset(self):
        """
        Raw overtime hours that fall AFTER sunset.

        Formula:
            if checkout_local > sunset:
                after = min(checkout_local - sunset, total_ot)
            else:
                after = 0
        """
        for rec in self:
            if rec.day_name not in WEEKDAYS:
                rec.overtime_hours_after_sunset = 0.0
                continue

            total_ot = rec.over_time_worked_hours
            if not (rec.check_out and rec.sunset and total_ot > 0):
                rec.overtime_hours_after_sunset = 0.0
                continue

            checkout_local = self._to_local_hour(rec.check_out)

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
            total = min(worked_hours, 8) x 2

        Saturday (day off) - no sunset split, counted as-is:
            1 <= worked_hours <= 3  ->  total = 4
            4 <= worked_hours <= 8  ->  total = 8
            worked_hours > 8        ->  total = worked_hours (actual value, no multiplier)
            otherwise               ->  total = 0
        """
        for rec in self:
            day = rec.day_name

            if day in WEEKDAYS:
                rec.total_overtime_hours = (
                    rec.weighted_overtime_before_sunset
                    + rec.weighted_overtime_after_sunset
                )

            elif day == 'Friday':
                capped = min(rec.worked_hours, STANDARD_HOURS)
                rec.total_overtime_hours = capped * 2

            elif day == 'Saturday':
                wh = rec.worked_hours
                if 1 <= wh <= 3:
                    rec.total_overtime_hours = 4.0
                elif 4 <= wh <= 8:
                    rec.total_overtime_hours = STANDARD_HOURS
                elif wh > STANDARD_HOURS:
                    rec.total_overtime_hours = wh
                else:
                    rec.total_overtime_hours = 0.0

            else:
                rec.total_overtime_hours = 0.0

    # --- Deduction ---

    @api.depends('check_in', 'check_out', 'worked_hours', 'day_name')
    def _compute_deduction_hours(self):
        """
        Calculate deduction hours based on three rules (weekdays only):

        1. Late arrival:
           If check-in is after 11:00 AM (local) -> deduct (check_in_local - 11.0)

        2. Early departure:
           If check-out is before 03:00 PM (local) -> deduct (15.0 - check_out_local)

        3. Short shift:
           If total worked_hours < 8 -> deduct (8.0 - worked_hours)

        Final deduction = max(rule1 + rule2, rule3)
        This avoids double-counting since rules 1 & 2 are usually
        the cause of the short shift captured by rule 3.
        """
        for rec in self:
            if not rec.check_in or rec.day_name not in WEEKDAYS:
                rec.deduction_hours = 0.0
                continue

            # All deduction rules require check_out to be present.
            # Without check_out we cannot determine the full picture.
            if not rec.check_out:
                rec.deduction_hours = 0.0
                continue

            checkin_local  = self._to_local_hour(rec.check_in)
            checkout_local = self._to_local_hour(rec.check_out)

            # Rule 1 — late arrival (strictly after 11:00 AM)
            late_arrival = checkin_local - LATE_CHECKIN_LIMIT if checkin_local > LATE_CHECKIN_LIMIT else 0.0

            # Rule 2 — early departure (strictly before 03:00 PM)
            early_departure = EARLY_CHECKOUT_LIMIT - checkout_local if checkout_local < EARLY_CHECKOUT_LIMIT else 0.0

            # Rule 3 — short shift (less than 8 hours worked)
            short_shift = max(STANDARD_HOURS - rec.worked_hours, 0.0)

            # Take the greater of (rule1 + rule2) vs rule3 to avoid double-counting
            rec.deduction_hours = max(late_arrival + early_departure, short_shift)