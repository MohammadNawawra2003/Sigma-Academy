from datetime import datetime, timedelta

import pytz

from odoo import Command
from odoo.tests import new_test_user

TZ = pytz.timezone('Asia/Hebron')


def local_to_utc(dt):
    return TZ.localize(dt).astimezone(pytz.utc).replace(tzinfo=None)


class TeachingCommonMixin:
    """Shared fixtures: one instructor, two families, students, the four session types."""

    @classmethod
    def setup_teaching(cls):
        env = cls.env
        if not env.company.chart_template:
            env['account.chart.template'].try_loading('generic_coa', env.company, install_demo=False)
        cls.instructor = new_test_user(
            env, login='teaching_instructor', name='Instructor Test', tz='Asia/Hebron',
            groups='teaching_course_management.group_teaching_instructor')
        cls.type_one = env.ref('teaching_course_management.appointment_type_one_to_one')
        cls.type_group = env.ref('teaching_course_management.appointment_type_group')
        cls.type_review = env.ref('teaching_course_management.appointment_type_review')
        cls.type_course = env.ref('teaching_course_management.appointment_type_course')
        (cls.type_one | cls.type_group | cls.type_review | cls.type_course).write(
            {'staff_user_ids': [Command.set(cls.instructor.ids)], 'slot_ids': [Command.clear()]})
        Partner = env['res.partner']
        cls.subject = env['teaching.subject'].create({'name': 'Physics (test)'})
        cls.grade = env['teaching.grade'].create({'name': 'Grade 11 (test)'})
        cls.curriculum = env['teaching.curriculum'].create({'name': 'IAL (test)'})
        cls.family_a = Partner.create({'name': 'Family A', 'is_company': True, 'email': 'family.a@example.com'})
        cls.family_b = Partner.create({'name': 'Family B', 'is_company': True, 'email': 'family.b@example.com'})
        cls.student_a1 = Partner.create({'name': 'Student A1', 'parent_id': cls.family_a.id, 'is_student': True,
                                         'grade_id': cls.grade.id, 'curriculum_id': cls.curriculum.id})
        cls.student_a2 = Partner.create({'name': 'Student A2', 'parent_id': cls.family_a.id, 'is_student': True})
        cls.student_b1 = Partner.create({'name': 'Student B1', 'parent_id': cls.family_b.id, 'is_student': True,
                                         'teaching_invoice_to': 'student', 'email': 'b1@example.com'})
        cls.extra_students = Partner.create([
            {'name': 'Extra %s' % i, 'parent_id': cls.family_b.id, 'is_student': True} for i in range(5)])

    @classmethod
    def make_session(cls, students, atype=None, start=None, duration=1.0, status='booked', user=None):
        start = start or local_to_utc(datetime.now().replace(hour=17, minute=0, second=0, microsecond=0) + timedelta(days=3))
        return cls.env['calendar.event'].with_user(user or cls.instructor).create({
            'name': 'Session',
            'user_id': cls.instructor.id,
            'appointment_type_id': (atype or cls.type_one).id,
            'subject_id': cls.subject.id,
            'teaching_student_ids': [Command.set(students.ids)],
            'start': start,
            'stop': start + timedelta(hours=duration),
            'duration': duration,
            'appointment_status': status,
        })
