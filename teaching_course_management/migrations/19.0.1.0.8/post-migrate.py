from odoo import SUPERUSER_ID, api
from odoo.tools.convert import convert_file


def migrate(cr, version):
    """The Teaching mail templates are noupdate: reload them so a shared session's emails stop
    naming every student (subject = session type, students = the family's own)."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    convert_file(env, 'teaching_course_management', 'data/mail_templates.xml', {}, mode='init', noupdate=True)
