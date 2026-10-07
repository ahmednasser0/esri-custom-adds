from odoo import fields, models


class HrLeaveType(models.Model):
    _inherit = 'hr.leave.type'

    is_overtime_request = fields.Boolean(
        string='Overtime Request',
        help="علّم النوع ده (Overtime Request) عشان يشتغل كتصريح إضافى. "
             "لما الموظف Office Assistant ياخد approve على إجازة من النوع ده "
             "فى يوم عادى، يتحسبله overtime فى اليوم ده. أى موظف بقاعدة تانية "
             "الإجازة دى مش بتعمل له حاجة. وفى كل الحالات النوع ده نفسه "
             "مابيتحسبش ساعات عمل ولا إجازة لأى حد.",
    )
