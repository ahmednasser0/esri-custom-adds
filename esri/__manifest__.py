{
    'name': "Esri HR Management",

    'summary': "Management HR Esri Group",

    'description': """
Management Attendance and Human Resources
    """,

    'author': "GBS Company",
    'website': "",

    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base','hr_attendance'],

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        'views/templates.xml',
        'views/attendance_hr.xml',
    ],
}

