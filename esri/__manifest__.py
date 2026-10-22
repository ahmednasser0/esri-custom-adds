{
    'name': "Esri HR Management",

    'summary': "Attendance, deductions and overtime, worked out automatically every day",

    # The page shown on the module's form is static/description/index.html.
    # This text is the fallback for anything that reads the manifest instead.
    'description': """
Esri HR Management
==================

Turns raw attendance into the three figures payroll needs - hours worked,
deduction and overtime - and keeps them correct as attendance, time off and
public holidays change.

* A daily report, a deduction and an overtime record per employee, per day
* Deduction tiers with a monthly grace, and a posted record that stays final
* Overtime earned against an approved request, weighted around sunset
* Separate weekend and public holiday rules, and per-employee rules
* Hours read on the employee's own clock, daylight saving included
* Working hours, weekend days, core hours, rates and tiers are all settings
    """,

    'author': "Ahmad Nasser",
    'website': "https://www.gbs-eg.com",

    'category': 'Human Resources',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',

    # any module necessary for this one to work correctly
    'depends': ['base', 'mail', 'hr_attendance', 'hr_holidays', 'hr_holidays_attendance', 'hr_work_entry'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/esri_config_view.xml',
        'views/attendance_hr.xml',
        'views/deduction.xml',
        'views/employee_hr.xml',
        'views/daily_report.xml',
        'views/overtime.xml',
        # 'views/hr_leave_allocations_view.xml',
    ],
}
