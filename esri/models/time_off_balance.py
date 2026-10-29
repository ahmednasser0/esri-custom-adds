import logging
from datetime import date, timedelta
from odoo import fields, models, api

_logger = logging.getLogger(__name__)



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
        related='employee_id.barcode',
        store=True,
        readonly=True,
        help="The employee's badge id, taken from the Barcode field on the employee",
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

            # A balance that is already spent has nothing left to take the
            # deduction from, so the sum is not done at all and the balance is
            # reported as it stands. Where there is a balance but less of it
            # than the deduction, what is left goes and the balance stops at
            # zero: the hours the balance could not cover are a matter for the
            # payroll, not something to show here as a negative balance.
            if current <= 0.0:
                rec.actual_balance = current
            else:
                rec.actual_balance = max(current - deduction, 0.0)

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
    def _refresh_employee_codes(self):
        """
        Work the Employee ID out again on every row.

        The column used to be filled by a compute that went looking through
        several identifier fields; it now simply follows the employee's
        barcode. A stored column keeps whatever it was last given, so rows
        written under the old rule would otherwise keep showing it.
        """
        try:
            rows = self.sudo().search([])
            if rows:
                self.env.add_to_compute(self._fields['employee_code'], rows)
                rows.flush_recordset()
        except Exception:
            _logger.warning("Could not refresh the Employee ID column",
                            exc_info=True)

    @api.model
    def _attach_to_employees_app(self):
        """
        Move the reports' parent menu into the Employees app.

        The app is looked up rather than named in the xml: a menu id that has
        moved between Odoo versions stops the module loading, so the menu is
        declared on its own and placed here, and anything that goes wrong
        leaves it where it was instead of failing the upgrade.
        """
        try:
            menu = self.env.ref('esri.menu_esri_attendance_reports', raise_if_not_found=False)
            root = self.env.ref('hr.menu_hr_root', raise_if_not_found=False)
            if not root:
                # Fall back to the app menu by name.
                root = self.env['ir.ui.menu'].sudo().search([
                    ('parent_id', '=', False),
                    ('name', '=', 'Employees'),
                ], limit=1)
            if menu and root and menu.parent_id.id != root.id:
                menu.sudo().parent_id = root.id
        except Exception:
            _logger.warning("Could not move the Time Off Balance menu into the "
                            "Employees app; it stays where it was declared",
                            exc_info=True)

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
