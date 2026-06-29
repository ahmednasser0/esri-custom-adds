import math
from datetime import timedelta

from odoo import fields, models, api

STANDARD_HOURS    = 8.0        # used to convert overtime hours -> allocation days
ANNUAL_LEAVE_NAME = 'Annual'   # search term used to locate the Annual Leave type
                                 # (hr.leave.type.name ilike this); adjust if your
                                 # leave type is named differently.


class Deduction(models.Model):
    _name        = 'deduction'
    _description = 'Deduction'

    name        = fields.Many2one('hr.employee', string='Employee')
    date        = fields.Date(string='Date')

    # ─── Computed fields ──────────────────────────────────────────────────────

    day_overtime = fields.Float(
        string='Overtime Hours',
        compute='_compute_day_values',
        store=False,
        help="Total weighted overtime hours for the selected employee on the selected date.",
    )
    day_deduction = fields.Float(
        string='Deduction Hours',
        compute='_compute_day_values',
        store=False,
        help="Total deduction hours for the selected employee on the selected date.",
    )
    type = fields.Selection(
        string='Type',
        selection=[
            ('overtime',             'Overtime'),
            ('deduction',            'Deduction'),
            ('overtime_deduction',   'Overtime and Deduction'),
        ],
        compute='_compute_day_values',
        store=True,
    )

    # ─── Allocation snapshot (auto-filled on create) ─────────────────────────

    allocation_before = fields.Float(
        string='Allocation Before (Hours)',
        readonly=True,
        help="Employee's Annual Leave allocation balance, converted to "
             "HOURS, BEFORE this record's overtime was added.",
    )
    allocation_after = fields.Float(
        string='Allocation After (Hours)',
        readonly=True,
        help="allocation_before + this record's overtime hours. The "
             "underlying hr.leave.allocation is stored in DAYS, so this "
             "value is allocation_after_days * 8 for display purposes.",
    )

    # ─── Internal tracking (not shown on the form) ───────────────────────────

    overtime_allocation_days = fields.Float(
        string='Overtime Allocation Days (granted)',
        default=0.0,
        help="Internal: number of DAYS that were added to the employee's "
             "Annual Leave allocation (number_of_days_display) because of "
             "this record's overtime. Used to reverse the exact same "
             "amount if this record is deleted.",
    )

    @api.depends('name', 'date')
    def _compute_day_values(self):
        for rec in self:
            if rec.name and rec.date:
                attendance = self.env['hr.attendance'].search([
                    ('employee_id',     '=', rec.name.id),
                    ('attendance_date', '=', rec.date),
                ], limit=1)
                ot  = attendance.total_overtime_hours if attendance else 0.0
                ded = attendance.deduction_hours      if attendance else 0.0

                rec.day_overtime  = ot
                rec.day_deduction = ded

                if ot > 0 and ded > 0:
                    rec.type = 'overtime_deduction'
                elif ot > 0:
                    rec.type = 'overtime'
                elif ded > 0:
                    rec.type = 'deduction'
                else:
                    rec.type = False
            else:
                rec.day_overtime  = 0.0
                rec.day_deduction = 0.0
                rec.type          = False

    # ─── Overtime -> Annual Leave Allocation sync ────────────────────────────

    def _get_annual_leave_type(self):
        """Locate the Annual Leave type used for overtime allocation."""
        return self.env['hr.leave.type'].sudo().search(
            [('name', 'ilike', ANNUAL_LEAVE_NAME)], limit=1
        )

    def _find_annual_allocation(self, employee, leave_type):
        """
        Search the employee's Annual Leave allocation, including archived
        ones, so we update the existing record instead of creating a
        duplicate.
        """
        return self.env['hr.leave.allocation'].sudo().with_context(
            active_test=False
        ).search([
            ('employee_id',       '=', employee.id),
            ('holiday_status_id', '=', leave_type.id),
        ], limit=1, order='id desc')

    def _apply_overtime_allocation(self):
        """
        For every record in self that has overtime (day_overtime > 0) AND
        has not already had its overtime applied (overtime_allocation_days
        is still 0):
          - snapshot the employee's current Annual Leave balance into
            allocation_before
          - compute allocation_after = allocation_before + the overtime
            HOURS as-is (no conversion to days)
          - add it to the employee's EXISTING hr.leave.allocation record.
          - if the employee has no Annual Leave allocation at all, do
            nothing (no new allocation is created).

        The overtime_allocation_days guard prevents the same record from
        granting allocation hours more than once (this method is called
        both from create() and from write(), since deduction records are
        sometimes created blank first and updated later once overtime
        becomes known).
        """
        leave_type = self._get_annual_leave_type()
        if not leave_type:
            return  # nothing to do if the leave type isn't configured

        for rec in self:
            if not rec.name or not (rec.day_overtime and rec.day_overtime > 0):
                continue

            if rec.overtime_allocation_days:
                # Already granted for this record - don't apply twice.
                continue

            allocation = self._find_annual_allocation(rec.name, leave_type)
            if not allocation:
                # No existing allocation for this employee -> do nothing.
                continue

            hours = rec.day_overtime

            current_value = allocation.number_of_hours_display
            new_value     = current_value + hours

            rec.write({
                'allocation_before':        current_value,
                'allocation_after':         new_value,
                'overtime_allocation_days': hours,
            })
            allocation.write({'number_of_hours_display': new_value})

    def _reverse_overtime_allocation(self):
        """
        For every record in self that previously granted allocation days,
        subtract that same amount back from the employee's Annual Leave
        allocation (never going below zero).
        """
        leave_type = self._get_annual_leave_type()
        if not leave_type:
            return

        for rec in self:
            if not rec.name or not (rec.overtime_allocation_days and rec.overtime_allocation_days > 0):
                continue

            allocation = self._find_annual_allocation(rec.name, leave_type)

            if allocation:
                new_value = max(allocation.number_of_hours_display - rec.overtime_allocation_days, 0.0)
                allocation.write({'number_of_hours_display': new_value})

    # ─── Deduction -> Annual Leave Time Off / Money Deduction sync ──────────

    def _get_remaining_annual_days(self, employee, leave_type):
        """
        Compute the employee's current Annual Leave balance the same way
        Odoo itself does: validated allocations minus already-taken leaves.
        """
        Allocation = self.env['hr.leave.allocation'].sudo()
        allocations = Allocation.with_context(active_test=False).search([
            ('employee_id',       '=', employee.id),
            ('holiday_status_id', '=', leave_type.id),
            ('state',             '=', 'validate'),
        ])
        total_allocated = sum(allocations.mapped('number_of_days'))

        Leave = self.env['hr.leave'].sudo()
        leaves = Leave.search([
            ('employee_id',       '=', employee.id),
            ('holiday_status_id', '=', leave_type.id),
            ('state',             'in', ['confirm', 'validate1', 'validate']),
        ])
        total_taken = sum(leaves.mapped('number_of_days'))

        return total_allocated - total_taken

    def _apply_deduction_leave(self):
        """
        For every record in self that has a deduction (day_deduction > 0):
          - convert the deduction hours to days
          - if the employee still has enough Annual Leave balance, create
            (and validate) an hr.leave request for that many days
          - otherwise, write the deduction HOURS (not days) onto the
            employee's hr.leave.allocation record, in the
            money_deduction_hours field, so payroll can deduct it from
            salary instead

        NOTE: assumes request_date_from / request_date_to are the actual
        field names on hr.leave in this Odoo version (daterange widget).
        Adjust if your installation uses date_from / date_to instead.
        """
        leave_type = self._get_annual_leave_type()
        if not leave_type:
            return

        Leave = self.env['hr.leave'].sudo()
        for rec in self:
            if not rec.name or not (rec.day_deduction and rec.day_deduction > 0):
                continue

            days_needed = rec.day_deduction / STANDARD_HOURS
            remaining   = self._get_remaining_annual_days(rec.name, leave_type)

            if remaining >= days_needed:
                date_from = rec.date or fields.Date.context_today(rec)
                # Simple approximation for deductions spanning more than one
                # day: extend the date range by the extra whole days needed.
                extra_days = max(math.ceil(days_needed) - 1, 0)
                date_to = date_from + timedelta(days=extra_days)

                leave = Leave.create({
                    'employee_id':       rec.name.id,
                    'holiday_status_id': leave_type.id,
                    'request_date_from': date_from,
                    'request_date_to':   date_to,
                })
                if hasattr(leave, 'action_approve'):
                    leave.action_approve()
                elif hasattr(leave, 'action_validate'):
                    leave.action_validate()
            else:
                # Not enough balance: flag the deduction hours on the
                # employee's allocation record instead of creating a leave.
                allocation = self._find_annual_allocation(rec.name, leave_type)
                if allocation:
                    allocation.write({
                        'money_deduction_hours': rec.day_deduction,
                    })

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._apply_overtime_allocation()
        records._apply_deduction_leave()
        return records

    def write(self, vals):
        res = super().write(vals)
        # Deduction records are sometimes created blank (no overtime yet)
        # by the attendance sync, then updated later once the day's
        # overtime becomes known - so we must re-check here too.
        self._apply_overtime_allocation()
        return res

    def unlink(self):
        self._reverse_overtime_allocation()
        return super().unlink()