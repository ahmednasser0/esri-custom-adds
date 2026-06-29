{
    'name': "Esri HR Management",

    'summary': "Management HR Esri Group",

    'description': """
Management Attendance and Human Resources
    """,

    'author': "Ahmad and Nouran",
    'website': "",

    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'hr_attendance', 'hr_holidays','hr_holidays_attendance'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/attendance_hr.xml',
        'views/deduction.xml',
        'views/employee_hr.xml',
        'views/overtime_report.xml',
        # 'views/hr_leave_allocations_view.xml',
    ],
}
