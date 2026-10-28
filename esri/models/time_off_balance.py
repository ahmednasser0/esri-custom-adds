from datetime import date, timedelta
from odoo import fields, models, api

# Candidate fields for the number the HR team calls the employee id. The first
# one the installed hr modules actually provide wins; change the order here if
# this database keeps it somewhere else.
EMPLOYEE_CODE_FIELDS = ('registration_number', 'identification_id', 'barcode')


class TimeOffBalance(models.Model):
    _name        = 'time.off.balance'
    _description = 'Employee Time Off Balance'
    _rec_name    = 'employee_id'
    _order       = 'date desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade',
    )
    employee_code = fields.Char(
        string='Employee ID',
        compute='_compute_employee_code',
        store=True,
        help="The employee's own identifier as it appears on the employee form",
    )
    date = fields.Date(string='Date', required=True)

    # Computed AND stored, like the daily report: the real sources are the
    # allocations, the leaves and the deduction, which the dependency graph
    # cannot see, so _ensure_record calls the computes directly.
    current_balance = fields.Float(
        string='Current Balance',
        compute='_compute_balance',
        store=True,
        help="The remaining balance of the configured annual leave type on this day, in hours",
    )
    deduction = fields.Float(
        string='Deduction',
        compute='_compute_balance',
        store=True,
        help="The deduction recorded for this employee on this day, in hours, or zero when there is none",
    )
    actual_balance = fields.Float(
        string='Actual Balance',
        compute='_compute_balance',
        store=True,
        help="Current Balance minus Deduction, in hours",
    )

    _sql_constraints = [
        ('unique_employee_date_balance', 'UNIQUE(employee_id, date)',
         'A time off balance already exists for this employee on this date.'),
    ]

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @api.model
    def _annual_leave_type(self):
        return self.env['esri.config'].sudo().get_config().annual_leave_type_id

    @api.model
    def _hours_per_day(self, employee):
        cal = employee.resource_calendar_id
        for value in (getattr(cal, 'hours_per_day', 0.0),
                      getattr(cal, 'daily_work_hours', 0.0)):
            if value:
                return value
        return 8.0

    @api.model
    def _allocation_hours(self, allocation, employee):
        """One allocation expressed in hours."""
        hours = getattr(allocation, 'number_of_hours_display', 0.0) or 0.0
        if hours:
            return hours
        return (allocation.number_of_days or 0.0) * self._hours_per_day(employee)

    @api.model
    def _balance_hours(self, employee, leave_type, target_date):
        """
        What is left of leave_type for employee on target_date, in hours:
        everything allocated and running on that day, less everything already
        taken by then.
        """
        if not leave_type:
            return 0.0

        allocations = self.env['hr.leave.allocation'].sudo().search([
            ('employee_id',       '=', employee.id),
            ('holiday_status_id', '=', leave_type.id),
            ('state',             '=', 'validate'),
            ('date_from',        '<=', target_date),
            '|', ('date_to', '=', False), ('date_to', '>=', target_date),
        ])
        allocated = sum(self._allocation_hours(a, employee) for a in allocations)

        # A leave counts from the day it starts; one still running on
        # target_date is counted in full, the way the balance on the Time Off
        # screen already reads it.
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id',        '=', employee.id),
            ('holiday_status_id',  '=', leave_type.id),
            ('state',              '=', 'validate'),
            ('request_date_from', '<=', target_date),
        ])
        taken = sum(l.number_of_hours or 0.0 for l in leaves)

        return allocated - taken

    @api.model
    def _deduction_hours(self, employee, target_date):
        record = self.env['deduction'].sudo().search([
            ('employee_id', '=', employee.id),
            ('date',        '=', target_date),
        ], limit=1)
        if not record:
            return 0.0
        return record.deduction or 0.0

    # ─── Compute ─────────────────────────────────────────────────────────────

    @api.depends('employee_id')
    def _compute_employee_code(self):
        for rec in self:
            code = False
            for fname in EMPLOYEE_CODE_FIELDS:
                if fname in rec.employee_id._fields:
                    value = rec.employee_id[fname]
                    if value:
                        code = value
                        break
            rec.employee_code = code

    @api.depends('employee_id', 'date')
    def _compute_balance(self):
        leave_type = self._annual_leave_type()
        for rec in self:
            if not rec.employee_id or not rec.date:
                rec.current_balance = 0.0
                rec.deduction       = 0.0
                rec.actual_balance  = 0.0
                continue

            current   = self._balance_hours(rec.employee_id, leave_type, rec.date)
            deduction = self._deduction_hours(rec.employee_id, rec.date)

            rec.current_balance = current
            rec.deduction       = deduction
            rec.actual_balance  = current - deduction

    # ─── Daily record creation ───────────────────────────────────────────────

    @api.model
    def _ensure_record(self, employee_id, target_date):
        """Create the (employee, date) row, or refresh the one already there."""
        existing = self.sudo().search([
            ('employee_id', '=', employee_id),
            ('date',        '=', target_date),
        ], limit=1)

        if not existing:
            return self.sudo().create({
                'employee_id': employee_id,
                'date':        target_date,
            })

        existing._compute_employee_code()
        existing._compute_balance()
        return existing

    @api.model
    def _create_balances_for_date(self, target_date):
        employees = self.env['hr.employee'].sudo().search([('active', '=', True)])
        for emp in employees:
            self._ensure_record(emp.id, target_date)

    @api.model
    def _cron_create_balances(self):
        self._create_balances_for_date(date.today() - timedelta(days=1))

    @api.model
    def generate_for_year(self, year=None, include_future=False):
        """
        Fill in the whole year, day by day, for every active employee.

        The cron only ever revisits yesterday, so this is how the year is built
        the first time, and how it is worked out again after something changed.
        Days that have not happened yet are left out unless asked for: their
        balance would only repeat today's with nothing deducted from it.
        """
        today = date.today()
        year  = int(year or today.year)

        day  = date(year, 1, 1)
        last = date(year, 12, 31)
        if not include_future:
            last = min(last, today)

        while day <= last:
            self._create_balances_for_date(day)
            day += timedelta(days=1)

    def action_generate_year(self):
        """Fill in the current year from the Actions menu of the list."""
        self.env['time.off.balance'].generate_for_year()
        return {
            'type': 'ir.actions.client',
            'tag':  'reload',
        }

    def unlink(self):
        """Deleting a row rebuilds it, the way the daily report does."""
        days = {
            (rec.employee_id.id, rec.date)
            for rec in self
            if rec.employee_id and rec.date
        }
        res = super().unlink()
        for employee_id, day in days:
            self._ensure_record(employee_id, day)
        return res
