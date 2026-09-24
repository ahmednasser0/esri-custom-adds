from datetime import timedelta
from odoo import api, models


class ResourceCalendarLeaves(models.Model):
    _inherit = 'resource.calendar.leaves'

    def _esri_affected_dates(self):
        """Set of dates covered by these company-wide (public holiday) entries."""
        dates = set()
        for leave in self:
            # Only company-wide entries are treated as public holidays.
            if leave.resource_id or not leave.date_from or not leave.date_to:
                continue
            d   = leave.date_from.date()
            end = leave.date_to.date()
            while d <= end:
                dates.add(d)
                d += timedelta(days=1)
        return dates

    def _esri_resync_daily_records(self, dates=None):
        """
        Recompute hr.overtime / daily.report / deduction for every employee on
        the dates these public holidays cover. Those models read
        resource.calendar.leaves through manual search() calls, so they are not
        part of Odoo's automatic @api.depends recompute graph and would keep
        stale values when a holiday is added, moved or removed after the fact.
        """
        # Never run while the registry is still loading (module install or
        # upgrade). At that point the esri models and their columns may not
        # exist yet, and localization modules that ship public holidays would
        # otherwise trigger a full resync in the middle of the install.
        if not self.env.registry.ready:
            return

        if dates is None:
            dates = self._esri_affected_dates()
        if not dates:
            return

        Overtime    = self.env['hr.overtime'].sudo()
        DailyReport = self.env['daily.report'].sudo()
        Deduction   = self.env['deduction'].sudo()
        employees   = self.env['hr.employee'].sudo().search([('active', '=', True)])

        # Re-saving the attendances makes hr.attendance recompute its own
        # stored fields (raw overtime), which the daily records build on.
        Attendance = self.env['hr.attendance'].sudo()
        for target_date in dates:
            atts = Attendance.search([('attendance_date', '=', target_date)])
            if atts:
                atts.modified(['check_in', 'check_out'])
                atts.flush_recordset()

            for emp in employees:
                Overtime._ensure_record(emp.id, target_date)
                DailyReport._ensure_record(emp.id, target_date)
                Deduction._ensure_record(emp.id, target_date)

    def _esri_is_public_holiday_vals(self, vals):
        """True when the written values could change public-holiday coverage."""
        return any(key in vals for key in ('resource_id', 'date_from', 'date_to'))

    # ─── Overrides ────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._esri_resync_daily_records()
        return records

    def write(self, vals):
        # Dates covered BEFORE the change also need refreshing, because a
        # holiday that moves away from a day must restore that day's values.
        old_dates = self._esri_affected_dates() if self._esri_is_public_holiday_vals(vals) else set()
        res = super().write(vals)
        if old_dates or self._esri_is_public_holiday_vals(vals):
            self._esri_resync_daily_records(old_dates | self._esri_affected_dates())
        return res

    def unlink(self):
        dates = self._esri_affected_dates()
        res = super().unlink()
        if dates:
            self._esri_resync_daily_records(dates)
        return res
