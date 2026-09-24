from datetime import timedelta
from odoo import models


class HrLeave(models.Model):
    _inherit = 'hr.leave'

    def _esri_resync_daily_records(self):
        """
        Force hr.overtime and daily.report to recompute for every
        (employee, date) covered by these leave requests. Their computed
        fields read hr.leave via manual search() calls, so they are not
        part of Odoo's automatic @api.depends recompute graph and never
        get refreshed just by approving/refusing a leave — this method
        does it explicitly.
        """
        Overtime    = self.env['hr.overtime'].sudo()
        DailyReport = self.env['daily.report'].sudo()
        Deduction   = self.env['deduction'].sudo()

        for leave in self:
            if not leave.employee_id or not leave.request_date_from or not leave.request_date_to:
                continue

            d = leave.request_date_from
            while d <= leave.request_date_to:
                Overtime._ensure_record(leave.employee_id.id, d)
                DailyReport._ensure_record(leave.employee_id.id, d)
                Deduction._ensure_record(leave.employee_id.id, d)
                d += timedelta(days=1)

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals:
            self._esri_resync_daily_records()
        return res
