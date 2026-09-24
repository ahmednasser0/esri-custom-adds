{
    'name': "Esri HR Management",

    'summary': "Management HR Esri Group",

    'description': """
Management Attendance and Human Resources
    """,

    'author': "Ahmad Nasser",
    'website': "",

    'category': 'Uncategorized',
    'version': '0.1',

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
