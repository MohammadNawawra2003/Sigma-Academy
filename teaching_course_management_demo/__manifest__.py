{
    'name': 'Teaching — Demo Data',
    'version': '19.0.1.0.0',
    'category': 'Services',
    'summary': 'Sigma Academy demo data: instructor, families, slots, sessions in every status, courses, invoices',
    'author': 'Al Shayeb Auditing and Accountancy Co',
    'license': 'OEEL-1',
    'depends': ['teaching_course_management'],
    # Demo data is off by default on Odoo 19 databases, so it is loaded as regular data.
    'data': [
        'data/company.xml',
        'data/users.xml',
        'data/config.xml',
        'data/partners.xml',
        'data/load.xml',
    ],
    'installable': True,
}
