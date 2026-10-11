from datetime import timedelta
from odoo import api, models


class HrLeave(models.Model):
    _inherit = 'hr.leave'

    def _esri_days(self):
        """The (employee, date) pairs these leaves cover."""
        days = set()
        for leave in self:
            if not leave.employee_id or not leave.request_date_from or not leave.request_date_to:
                continue
            d = leave.request_date_from
            while d <= leave.request_date_to:
                days.add((leave.employee_id.id, d))
                d += timedelta(days=1)
        return days

    def _esri_resync_days(self, days=None):
        """
        Recompute hr.overtime, daily.report and deduction for the days these
        leaves cover.

        Those models read hr.leave through manual search() calls, which Odoo's
        dependency graph cannot see, so nothing refreshes them when a leave
        appears, is approved or goes away. Their fields are stored columns, so
        a stale figure stays on screen until something recomputes it.
        """
        if days is None:
            days = self._esri_days()
        if not days:
            return

        Overtime    = self.env['hr.overtime'].sudo()
        DailyReport = self.env['daily.report'].sudo()
        Deduction   = self.env['deduction'].sudo()

        for employee_id, day in days:
            Overtime._ensure_record(employee_id, day)
            DailyReport._ensure_record(employee_id, day)
            Deduction._ensure_record(employee_id, day)

    # Kept under its old name for anything still calling it.
    def _esri_resync_daily_records(self):
        self._esri_resync_days()

    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        # A leave can be created already approved, by an import or by a manager
        # entering it directly, and that never passes through write().
        leaves.filtered(lambda l: l.state == 'validate')._esri_resync_days()
        return leaves

    def write(self, vals):
        touches = {'state', 'request_date_from', 'request_date_to', 'employee_id'} & set(vals)
        # The dates or the employee can move. The days the leave used to cover
        # have to be resynced too, or they keep figures that still count it.
        before = self._esri_days() if touches else set()

        res = super().write(vals)

        if touches:
            self._esri_resync_days(before | self._esri_days())
        return res

    def unlink(self):
        # The days have to be read BEFORE the leaves go, and resynced AFTER, so
        # the recomputation no longer sees them. Without this a deleted leave
        # leaves its hours behind in the overtime and the daily report.
        days = self._esri_days()
        res = super().unlink()
        if days:
            self._esri_resync_days(days)
        return res
