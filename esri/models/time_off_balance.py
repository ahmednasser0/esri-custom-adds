import logging
from odoo import fields, models, api

_logger = logging.getLogger(__name__)

# The three models a balance line can stand for, and the source value each one
# is written under.
SOURCES = {
    'hr.leave':            'time_off',
    'hr.leave.allocation': 'allocation',
    'deduction':           'deduction',
}


class TimeOffBalance(models.Model):
    _name        = 'time.off.balance'
    _description = 'Employee Time Off Balance'
    _rec_name    = 'employee_id'
    _order       = 'start_date desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade',
        index=True,
    )
    employee_code = fields.Char(
        string='Employee ID',
        related='employee_id.barcode',
        store=True,
        readonly=True,
        help="The employee's badge id, taken from the Barcode field on the employee",
    )
    leave_type_id = fields.Many2one(
        'hr.leave.type',
        string='Time Off Type',
        ondelete='set null',
        help="The time off type the line is for: the type on the time off or the allocation, and for a deduction the Annual Leave Time Off Type set in Esri Configurations",
    )
    start_date = fields.Datetime(
        string='Start Date',
        help="When the time off or the allocation starts, or the day the deduction was recorded",
    )
    end_date = fields.Datetime(
        string='End Date',
        help="When the time off or the allocation ends; a deduction line has none",
    )
    number_of_days = fields.Float(
        string='Number of Days',
        help="The days the line moves: an allocation adds them and counts up, while time off and a deduction take them away and count down",
    )
    status = fields.Char(
        string='Status',
        help="The state of the source record, as that record's own screen shows it",
    )
    description = fields.Char(
        string='Description',
        help="The description written on the source record",
    )
    source = fields.Selection(
        [
            ('time_off',   'Time Off'),
            ('allocation', 'Allocation'),
            ('deduction',  'Deduction'),
        ],
        string='Source',
        required=True,
        index=True,
        help="Which model the line was taken from",
    )

    # The line and the record it stands for. Kept so the line can be found
    # again and rewritten whenever that record changes.
    source_model = fields.Char(string='Source Model', required=True, index=True)
    source_res_id = fields.Integer(string='Source Record', required=True, index=True)

    _unique_source_record = models.Constraint(
        'UNIQUE(source_model, source_res_id)',
        'A time off balance line already exists for this source record.',
    )

    # ─── Reading one source record ───────────────────────────────────────────

    @api.model
    def _annual_leave_type(self):
        return self.env['esri.config'].sudo().get_config().annual_leave_type_id

    @api.model
    def _state_label(self, record):
        """The source record's state as its own screen shows it."""
        field = record._fields.get('state')
        if not field:
            return False
        value = record.state
        if not value:
            return False
        try:
            selection = dict(field._description_selection(record.env))
            return selection.get(value, value)
        except Exception:
            return value

    @api.model
    def _first_value(self, record, names):
        """The first of names the record actually carries, or False.

        Odoo renames these between versions - an allocation's validity period
        has gone by more than one name - so the field is looked for rather
        than named once and hoped for.
        """
        for name in names:
            if name in record._fields:
                return record[name] or False
        return False

    @api.model
    def _line_values(self, record):
        """The balance line for one source record, or None when there is none."""
        model = record._name
        source = SOURCES.get(model)
        if not source:
            return None

        employee = record.employee_id
        if not employee:
            return None

        values = {
            'employee_id':   employee.id,
            'source':        source,
            'source_model':  model,
            'source_res_id': record.id,
            'status':        self._state_label(record),
        }

        if source == 'deduction':
            days = record.deducted_days or 0.0
            # A day that was never deducted has nothing to say here, so no line
            # stands for it; one that had a line and no longer deducts anything
            # loses it, because _sync_records drops a line left with nothing.
            if not days:
                return None
            # A deduction is a single day, so it has a start and no end, and it
            # is counted against the configured annual leave type.
            values.update({
                'leave_type_id':  self._annual_leave_type().id or False,
                'start_date':     record.date or False,
                'end_date':       False,
                'number_of_days': days,
                'description':    record.description or False,
            })
        else:
            values.update({
                'leave_type_id':  record.holiday_status_id.id or False,
                'start_date':     self._first_value(record, ('date_from', 'start_date')),
                'end_date':       self._first_value(record, ('date_to', 'end_date')),
                'number_of_days': self._first_value(
                    record, ('number_of_days_display', 'number_of_days')) or 0.0,
                'description':    record.name or False,
            })

        # The column reads as a movement, not as a quantity: an allocation puts
        # days in and counts up, time off and a deduction take them out and
        # count down. The source always reports its own figure as a positive
        # number, so the sign is put on here rather than relied on.
        days = abs(values.get('number_of_days') or 0.0)
        values['number_of_days'] = days if source == 'allocation' else -days

        return values

    # ─── Writing the lines ───────────────────────────────────────────────────

    @api.model
    def _sync_records(self, records):
        """Write, rewrite or drop the balance line of each source record."""
        if not records:
            return

        model = records._name
        lines = self.sudo().search([
            ('source_model',  '=', model),
            ('source_res_id', 'in', records.ids),
        ])
        by_res_id = {line.source_res_id: line for line in lines}

        for record in records:
            line = by_res_id.get(record.id)
            values = self._line_values(record)
            if values is None:
                # Nothing to stand for any more - an employee was taken off the
                # record, say - so the line goes with it.
                if line:
                    line.sudo().unlink()
                continue
            if line:
                line.sudo().write(values)
            else:
                self.sudo().create(values)

    @api.model
    def _drop_records(self, records):
        """Remove the lines of source records that are being deleted."""
        if not records:
            return
        self.sudo().search([
            ('source_model',  '=', records._name),
            ('source_res_id', 'in', records.ids),
        ]).unlink()

    @api.model
    def _rebuild_all(self):
        """
        Write every line again from the three source models.

        Runs on install and on upgrade, so the report is complete the first
        time it is opened rather than only from the next change onwards. Each
        model goes in its own savepoint: one that cannot be read leaves the
        other two standing instead of failing the upgrade.
        """
        for model in SOURCES:
            try:
                with self.env.cr.savepoint():
                    if model not in self.env:
                        continue
                    records = self.env[model].sudo().search([])
                    # Lines whose source record is gone have nothing left to
                    # stand for.
                    self.sudo().search([
                        ('source_model', '=', model),
                        ('source_res_id', 'not in', records.ids),
                    ]).unlink()
                    self._sync_records(records)
            except Exception:
                _logger.warning("Could not rebuild the Time Off Balance lines "
                                "for %s", model, exc_info=True)


class TimeOffBalanceSourceMixin(models.AbstractModel):
    """
    Keeps a source model's balance lines in step with it.

    Inherited by the three models rather than written out three times: every
    create, every write and every delete is mirrored into time.off.balance,
    and a mirror that fails is logged and left rather than taking the save of
    the leave, the allocation or the deduction down with it.
    """
    _name        = 'time.off.balance.source.mixin'
    _description = 'Time Off Balance Source'

    def _mirror_to_balance(self, dropping=False):
        Balance = self.env['time.off.balance'].sudo()
        try:
            # The savepoint matters: a failed query aborts the whole
            # transaction, so catching the error without rolling back to here
            # would only move the failure to the next statement.
            with self.env.cr.savepoint():
                if dropping:
                    Balance._drop_records(self)
                else:
                    Balance._sync_records(self)
        except Exception:
            _logger.warning(
                "Could not update the Time Off Balance lines for %s %s",
                self._name, self.ids, exc_info=True,
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._mirror_to_balance()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._mirror_to_balance()
        return res

    def unlink(self):
        self._mirror_to_balance(dropping=True)
        return super().unlink()


class HrLeaveBalanceSource(models.Model):
    _name    = 'hr.leave'
    _inherit = ['hr.leave', 'time.off.balance.source.mixin']


class HrLeaveAllocationBalanceSource(models.Model):
    _name    = 'hr.leave.allocation'
    _inherit = ['hr.leave.allocation', 'time.off.balance.source.mixin']
