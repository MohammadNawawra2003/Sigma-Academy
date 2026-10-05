from datetime import date, datetime

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common import TeachingCommonMixin


@tagged('post_install', '-at_install', 'teaching')
class TestTeachingCourse(TeachingCommonMixin, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_teaching()

    def _course(self, **vals):
        return self.env['teaching.course'].with_user(self.instructor).create({
            'name': 'IGCSE Maths Revision',
            'subject_id': self.subject.id,
            'curriculum_id': self.curriculum.id,
            'grade_id': self.grade.id,
            'instructor_id': self.instructor.id,
            'mon': True,
            'wed': True,
            'start_hour': 18.0,
            'start_date': date(2026, 11, 2),
            'end_type': 'end_date',
            'until': date(2026, 11, 25),
            'capacity': 3,
            'student_ids': [Command.set(self.student_a1.ids)],
            **vals,
        })

    @freeze_time('2026-10-20 10:00:00')
    def test_generate_independent_sessions(self):
        """W24 / R11: Mon+Wed 18:00, 2–25 Nov = 8 independent sessions; generating twice is refused."""
        course = self._course()
        course.action_confirm()
        course.action_generate_sessions()
        sessions = course.session_ids.sorted('start')
        self.assertEqual(len(sessions), 8)
        self.assertEqual([s.start.day for s in sessions], [2, 4, 9, 11, 16, 18, 23, 25])
        self.assertEqual(sessions[0].start.hour, 16, '18:00 Hebron (UTC+2 in November) = 16:00 UTC')
        self.assertEqual(course.state, 'running')
        self.assertTrue(all(s.teaching_student_ids == self.student_a1 for s in sessions))
        self.assertEqual(set(sessions.mapped('appointment_status')), {'booked'})
        self.assertAlmostEqual(sessions[0].teaching_price_unit, 70.0)
        with self.assertRaises(UserError):
            course.action_generate_sessions()
        sessions[2].action_teaching_cancel()
        self.assertEqual(len(course.session_ids.filtered('active')), 7)
        self.assertEqual(sessions[3].appointment_status, 'booked')

    @freeze_time('2026-11-10 10:00:00')
    def test_enrol_future_sessions_and_capacity(self):
        """W25: enrolment adds the student to future sessions only and respects capacity."""
        with freeze_time('2026-10-20 10:00:00'):
            course = self._course()
            course.action_confirm()
            course.action_generate_sessions()
        course._enrol(self.student_a2)
        now = datetime(2026, 11, 10, 10)
        future = course.session_ids.filtered(lambda s: s.start > now)
        self.assertTrue(all(self.student_a2 in s.teaching_student_ids for s in future))
        past = course.session_ids.filtered(lambda s: s.start < now)
        self.assertTrue(past and not any(self.student_a2 in s.teaching_student_ids for s in past))
        course._enrol(self.student_b1)
        self.assertEqual(course.seats_available, 0)
        with self.assertRaises(UserError):
            course._enrol(self.extra_students[0])
        with self.assertRaises(ValidationError):
            course.student_ids = [Command.link(self.extra_students[0].id)]

    def test_publish_announcement(self):
        """W26: a course announcement is a published post in the Announcements blog."""
        course = self._course()
        course.action_confirm()
        course.with_env(self.env).action_publish_announcement()  # admin-only button
        post = self.env['blog.post'].search([('teaching_course_id', '=', course.id)])
        self.assertEqual(post.blog_id, self.env.ref('teaching_course_management.blog_announcements'))
        self.assertTrue(post.is_published)

    def test_constraints(self):
        with self.assertRaises(UserError):
            course = self._course(mon=False, wed=False)
            course.action_confirm()
            course.action_generate_sessions()
        with self.assertRaises(ValidationError):
            self._course(until=date(2026, 10, 1))
