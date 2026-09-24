from odoo import models, fields, api


class EsriConfig(models.Model):
    _name = 'esri.config'
    _description = 'Esri HR Configurations'

    name = fields.Char(default='Esri HR Configurations', readonly=True)

    overtime_rate_before_sunset = fields.Float(
        string='Overtime Rate Before Sunset',
        default=1.35,
        required=True,
    )
    overtime_rate_after_sunset = fields.Float(
        string='Overtime Rate After Sunset',
        default=1.70,
        required=True,
    )
    saturday_first_rate = fields.Float(
        string='Saturday First Rate (1–3 hrs)',
        default=0.5,
        required=True,
    )
    saturday_second_rate = fields.Float(
        string='Saturday Second Rate (4–8 hrs)',
        default=1.0,
        required=True,
    )
    friday_rate = fields.Float(
        string='Friday Rate',
        default=2.0,
        required=True,
    )
    overtime_cutoff = fields.Float(
        string='Overtime Cutoff Time',
        default=19.0,
        required=True,
        help="الوقت الأقصى لحساب الـ overtime (24h format). مثال: 19.0 = 7 مساء.",
    )

    max_worked_hours = fields.Float(
        string='Max Worked Hours',
        default=12.0,
        required=True,
        help="أقصى عدد ساعات عمل ممكن تتسجل للموظف فى اليوم الواحد (يستخدم فى الـ Daily Report).",
    )

    # ─── Deduction rates (fraction of the daily work hours) ─────────────────

    company_day_hours = fields.Float(
        string='Company Day Hours',
        default=8.0,
        required=True,
        help="عدد ساعات اليوم اللى بيتحسب عليها الخصم الفعلى (الشرائح والغياب). "
             "تقييم الحضور/الانصراف (Core Check / Daily Check) لسه بيعتمد على "
             "Daily Work Hours بتاع الـ Working Schedule؛ الحقل ده بس بيتحكم "
             "فى عدد الساعات اللى بتتضرب فيها نسب الخصم.",
    )
    deduction_quarter_rate = fields.Float(
        string='First Tier Deduction Rate (1–15 min)',
        default=0.25,
        required=True,
        help="نسبة الخصم من ساعات اليوم للتأخير أو الانصراف المبكر من 1 لـ 15 دقيقة. مثال: 0.25 = ربع يوم.",
    )
    deduction_half_rate = fields.Float(
        string='Second Tier Deduction Rate (16–60 min)',
        default=0.5,
        required=True,
        help="نسبة الخصم من ساعات اليوم للتأخير أو الانصراف المبكر من 16 لـ 60 دقيقة. مثال: 0.5 = نص يوم.",
    )
    deduction_full_rate = fields.Float(
        string='Third Tier Deduction Rate (> 60 min)',
        default=1.0,
        required=True,
        help="نسبة الخصم من ساعات اليوم للتأخير أو الانصراف المبكر أكثر من ساعة. مثال: 1.0 = يوم كامل.",
    )
    absence_deduction_rate = fields.Float(
        string='Absence Deduction Rate',
        default=1.25,
        required=True,
        help="نسبة الخصم من ساعات اليوم في حالة الغياب بدون إذن. مثال: 1.25 = يوم وربع.",
    )

    @api.model
    def get_config(self):
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return config

    @api.model_create_multi
    def create(self, vals_list):
        existing = self.search([], limit=1)
        if existing:
            existing.write(vals_list[0] if vals_list else {})
            return existing
        return super().create(vals_list)

    def unlink(self):
        raise models.UserError("لا يمكن حذف إعدادات النظام.")
