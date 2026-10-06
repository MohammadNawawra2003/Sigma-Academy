from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo import Command, http
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from .common import TeachingCommonMixin, local_to_utc


def next_monday(hour, minute=0):
    day = date.today() + timedelta(days=7 - date.today().weekday() + 7)
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


@tagged('post_install', '-at_install', 'teaching')
class TestTeachingSlotRules(TeachingCommonMixin, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_teaching()

    def _blocked(self, start_local, students, hours=1):
        start = local_to_utc(start_local)
        return self.type_one._teaching_slot_blocked(self.instructor, start, start + timedelta(hours=hours), students)

    def test_gap_other_student_only(self):
        """R3: 15 minutes after/before another student's session; none for the same student."""
        self.make_session(self.student_a1, start=local_to_utc(next_monday(17)))
        self.assertEqual(self._blocked(next_monday(18, 10), self.student_b1), 'gap')
        self.assertEqual(self._blocked(next_monday(15, 50), self.student_b1), 'gap')
        self.assertFalse(self._blocked(next_monday(18), self.student_a1), 'back to back for the same student')
        self.assertFalse(self._blocked(next_monday(18, 15), self.student_b1), 'exactly 15 minutes is enough')
        self.env['ir.config_parameter'].set_param('teaching_course_management.gap_minutes', 0)
        self.assertFalse(self._blocked(next_monday(18, 10), self.student_b1))

    @freeze_time('2026-11-02 11:00:00')  # 13:00 in Hebron
    def test_cutoff_after_noon(self):
        """R4: same-day booking closes at 12:00; tomorrow stays open."""
        self.assertEqual(self._blocked(datetime(2026, 11, 2, 16), self.student_a1), 'cutoff')
        self.assertFalse(self._blocked(datetime(2026, 11, 3, 16), self.student_a1))

    @freeze_time('2026-11-02 08:00:00')  # 10:00 in Hebron
    def test_cutoff_before_noon(self):
        self.assertFalse(self._blocked(datetime(2026, 11, 2, 16), self.student_a1))

    def test_only_published_slots_are_valid(self):
        """The submit check accepts a published free slot only, and applies the gap to it."""
        start = local_to_utc(next_monday(17))
        self.type_one.slot_ids = [Command.create({
            'slot_type': 'unique', 'start_datetime': start, 'end_datetime': start + timedelta(hours=1)})]
        appt = self.type_one.with_context(teaching_student_ids=self.student_b1.ids)
        check = appt._check_appointment_is_valid_slot
        self.assertTrue(check(self.instructor, self.env['appointment.resource'], 1, 'Asia/Hebron', start, 1.0, False))
        self.assertFalse(check(self.instructor, self.env['appointment.resource'], 1, 'Asia/Hebron',
                               start + timedelta(hours=2), 1.0, False), 'not published')
        self.make_session(self.student_a1, start=start - timedelta(minutes=70), duration=1.0)
        self.assertFalse(check(self.instructor, self.env['appointment.resource'], 1, 'Asia/Hebron', start, 1.0, False),
                         'another student ends 10 minutes before')


@tagged('post_install', '-at_install', 'teaching')
class TestTeachingBookingFlow(TeachingCommonMixin, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_teaching()
        cls.portal_family_a = new_test_user(cls.env, login='family_a_portal', groups='base.group_portal',
                                            partner_id=cls.family_a.id)

    def _publish(self, *starts_local, atype=None):
        (atype or self.type_one).slot_ids = [Command.create({
            'slot_type': 'unique',
            'start_datetime': local_to_utc(s),
            'end_datetime': local_to_utc(s) + timedelta(hours=1),
        }) for s in starts_local]

    def _submit(self, start_local, student, two_hours=False, atype=None, asked_capacity=1):
        self.authenticate('family_a_portal', 'family_a_portal')
        data = {
            'csrf_token': http.Request.csrf_token(self),
            'datetime_str': start_local.strftime('%Y-%m-%d %H:%M:%S'),
            'duration_str': '1.0',
            'name': 'Family A',
            'email': 'family.a@example.com',
            'staff_user_id': self.instructor.id,
            'asked_capacity': asked_capacity,
        }
        if student:
            data['teaching_student_id'] = student.id
        if two_hours:
            data['teaching_two_hours'] = '1'
        return self.url_open(f'/appointment/{(atype or self.type_one).id}/submit', data=data)

    def _events(self):
        return self.env['calendar.event'].search(
            [('appointment_type_id', '=', self.type_one.id), ('teaching_student_ids', 'in', self.family_a.child_ids.ids)],
            order='start')

    def test_book_one_hour_is_pending(self):
        """W5: a website booking lands in Pending Approval for the chosen student."""
        self._publish(next_monday(17))
        self._submit(next_monday(17), self.student_a2)
        event = self._events()
        self.assertEqual(len(event), 1)
        self.assertEqual(event.appointment_status, 'request')
        self.assertEqual(event.teaching_student_ids, self.student_a2)
        self.assertIn(self.family_a, event.partner_ids)

    def test_book_two_hours_back_to_back(self):
        """W6 / R5: 2 hours = two 1-hour requests back to back."""
        self._publish(next_monday(17), next_monday(18))
        self._submit(next_monday(17), self.student_a1, two_hours=True)
        events = self._events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].stop, events[1].start)
        self.assertEqual(set(events.mapped('appointment_status')), {'request'})

    def test_book_two_hours_next_hour_missing(self):
        """W6: if the second hour is not published/free, nothing is booked at all."""
        self._publish(next_monday(17))
        self._submit(next_monday(17), self.student_a1, two_hours=True)
        self.assertFalse(self._events())

    def test_crafted_post_unpublished_time(self):
        """A crafted POST for a time that was never published creates nothing."""
        self._publish(next_monday(17))
        self._submit(next_monday(20), self.student_a1)
        self.assertFalse(self._events())

    def test_other_family_student_refused(self):
        """A guardian cannot book for another family's student."""
        self._publish(next_monday(17))
        response = self._submit(next_monday(17), self.student_b1)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.env['calendar.event'].search([('teaching_student_ids', 'in', self.student_b1.ids)]))

    def test_public_user_must_login(self):
        """Teaching booking forms require login."""
        self.authenticate(None, None)
        response = self.url_open(
            f'/appointment/{self.type_one.id}/info?date_time=2030-01-01+10:00:00&duration=1.0&staff_user_id={self.instructor.id}',
            allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('/web/login', response.headers['Location'])

    def test_group_booking_takes_one_seat(self):
        """R10: one website booking is one student, whatever "Number of people" is posted."""
        self._publish(next_monday(17), atype=self.type_group)
        self._submit(next_monday(17), self.student_a1, atype=self.type_group, asked_capacity=5)
        event = self.env['calendar.event'].search([
            ('appointment_type_id', '=', self.type_group.id), ('teaching_student_ids', 'in', self.family_a.child_ids.ids)])
        self.assertEqual(len(event), 1)
        self.assertEqual(event.booking_line_ids.mapped('capacity_reserved'), [1])
        self.assertEqual(event.teaching_student_ids, self.student_a1)

    def test_guardian_must_pick_a_student(self):
        """A guardian's booking without a student is refused: the guardian is never billed as the student."""
        self._publish(next_monday(17))
        response = self._submit(next_monday(17), None)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._events())
        self.assertFalse(self.env['calendar.event'].search([('partner_ids', 'in', self.family_a.ids), ('is_teaching', '=', True)]))
