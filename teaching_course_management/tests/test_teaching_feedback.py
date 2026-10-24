"""Sari's UAT notes of 10 Oct 2026 (Task 59). One test class per note, each protecting what he asked for."""
import json
import uuid
from datetime import timedelta
from urllib.parse import quote

from odoo import Command, fields, http
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .common import TeachingCommonMixin, local_to_utc
from .test_teaching_booking import next_monday


@tagged('post_install', '-at_install', 'teaching')
class TestTeachingFeedbackOct10(TeachingCommonMixin, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_teaching()
        cls.portal_family_a = new_test_user(cls.env, login='family_a_portal', groups='base.group_portal',
                                            partner_id=cls.family_a.id)
        cls.portal_student_a1 = new_test_user(cls.env, login='student_a1_portal', groups='base.group_portal',
                                              partner_id=cls.student_a1.id)
        cls.portal_family_b = new_test_user(cls.env, login='family_b_portal', groups='base.group_portal',
                                            partner_id=cls.family_b.id)

    def _publish(self, *starts_local, atype=None):
        atype = atype or self.type_one
        atype.slot_ids = [Command.create({
            'slot_type': 'unique',
            'start_datetime': local_to_utc(s),
            'end_datetime': local_to_utc(s) + timedelta(hours=1),
        }) for s in starts_local]
        return atype.slot_ids.sorted('start_datetime')

    def _submit(self, login, start_local, student=None, two_hours=False, atype=None):
        self.authenticate(login, login)
        data = {
            'csrf_token': http.Request.csrf_token(self),
            'datetime_str': start_local.strftime('%Y-%m-%d %H:%M:%S'),
            'duration_str': '1.0',
            'name': 'Booker',
            'email': 'booker@example.com',
            'staff_user_id': self.instructor.id,
            'asked_capacity': 1,
        }
        if student:
            data['teaching_student_id'] = student.id
        if two_hours:
            data['teaching_two_hours'] = '1'
        return self.url_open(f'/appointment/{(atype or self.type_one).id}/submit', data=data, allow_redirects=False)

    def _events(self, student, atype=None):
        return self.env['calendar.event'].search([
            ('appointment_type_id', '=', (atype or self.type_one).id), ('teaching_student_ids', 'in', student.ids)],
            order='start')

    # ---------------------------------------------------------------- note 1: no approval page for the student

    def test_booking_lands_on_student_session_page(self):
        """Note 1: after booking, the family lands on its own session page showing Pending Approval,
        not on a page about approving."""
        self._publish(next_monday(17))
        response = self._submit('family_a_portal', next_monday(17), self.student_a2)
        event = self._events(self.student_a2)
        self.assertEqual(len(event), 1)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith(f'/my/sessions/{event.id}?booked=1'),
                        response.headers['Location'])
        page = self.url_open(f'/my/sessions/{event.id}?booked=1').text
        self.assertIn('Pending Approval', page)
        self.assertIn('Request sent', page)
        self.assertNotIn('Approve', page)

    def test_student_cannot_approve(self):
        """Note 1: approving is the teacher's job; a portal user can neither open the list nor approve."""
        event = self.make_session(self.student_a1, start=fields.Datetime.now() + timedelta(days=4), status='request')
        with self.assertRaises(AccessError):
            event.with_user(self.portal_student_a1).action_teaching_approve()
        self.authenticate('student_a1_portal', 'student_a1_portal')
        response = self.url_open('/odoo/action-teaching_course_management.action_teaching_sessions_pending')
        self.assertNotIn('/odoo/action-teaching_course_management.action_teaching_sessions_pending', response.url)
        self.assertEqual(event.appointment_status, 'request')

    # ---------------------------------------------------------------- note 2: one calendar per session type

    def test_book_link_lists_teaching_types_only(self):
        """Note 2: "Book a Session" opens the teaching session types, each with its own calendar."""
        other = self.env['appointment.type'].create({'name': 'Dentist (test)', 'is_published': True})
        self.authenticate('family_a_portal', 'family_a_portal')
        home = self.url_open('/my').text
        published = (self.type_one | self.type_group | self.type_review).filtered('is_published')
        book_url = '/appointment?filter_appointment_type_ids=%s' % quote(json.dumps(published.ids))
        self.assertIn(book_url.replace('&', '&amp;'), home)
        page = self.url_open(book_url).text
        self.assertIn(self.type_one.name, page)
        self.assertIn(self.type_group.name, page)
        self.assertNotIn(other.name, page)

    def test_type_page_switches_between_types(self):
        """Note 2: every type page offers the other types as tabs, the current one marked active."""
        self.authenticate('family_a_portal', 'family_a_portal')
        page = self.url_open(f'/appointment/{self.type_one.id}').text
        self.assertIn('o_teaching_type_tabs', page)
        self.assertIn(f'href="/appointment/{self.type_group.id}"', page)
        self.assertIn(f'aria-current="page" href="/appointment/{self.type_one.id}"', page)

    def test_group_calendar_shows_hours_without_picking_people(self):
        """Note 2/6: on the Group calendar a day click shows its hours at once. Natively the hours stay
        hidden until "Number of people" is picked, and a family books one seat anyway."""
        self._publish(next_monday(15), atype=self.type_group)
        self.authenticate('family_a_portal', 'family_a_portal')
        page = self.url_open(f'/appointment/{self.type_group.id}').text
        self.assertRegex(page, r'<option[^>]*value="1"[^>]*selected="selected"')
        self.assertNotRegex(page, r'<option[^>]*value="0"[^>]*selected="selected"')

    def test_each_type_offers_only_its_own_slots(self):
        """Note 2: the 1-to-1 calendar never shows Group availability and vice versa."""
        self._publish(next_monday(10))
        self._publish(next_monday(15), atype=self.type_group)

        def offered(atype):
            months = atype._get_appointment_slots('Asia/Hebron', filter_users=self.instructor)
            return {slot['datetime'] for month in months for week in month['weeks'] for day in week
                    for slot in day.get('slots', [])}
        one, group = offered(self.type_one), offered(self.type_group)
        self.assertEqual({s[11:16] for s in one}, {'10:00'})
        self.assertEqual({s[11:16] for s in group}, {'15:00'})

    # ---------------------------------------------------------------- note 3: cancellation (current policy only)

    def test_portal_cancel_returns_to_my_sessions(self):
        """Note 3: cancelling a pending request from the portal returns to My Sessions, not to a booking page."""
        event = self.make_session(self.student_a1, start=fields.Datetime.now() + timedelta(days=4), status='request')
        event.access_token = uuid.uuid4().hex
        self.authenticate('family_a_portal', 'family_a_portal')
        response = self.url_open(f'/calendar/cancel/{event.access_token}?partner_id={self.family_a.id}',
                                 allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith('/my/sessions?withdrawn=1'), response.headers['Location'])
        self.assertEqual(event.with_context(active_test=False).appointment_status, 'cancelled')

    def test_no_portal_cancel_explains_why(self):
        """Note 3: when the family can no longer cancel, the page says to contact the academy."""
        event = self.make_session(self.student_a1, start=fields.Datetime.now() + timedelta(hours=2))
        self.authenticate('family_a_portal', 'family_a_portal')
        page = self.url_open(f'/my/sessions/{event.id}').text
        self.assertNotIn('/calendar/cancel/', page)
        self.assertIn('contact the academy', page)

    # ------------------------------------------- note 3 (clarified 23 Oct): the family withdraws a pending request

    def _withdraw(self, login, event, partner):
        self.authenticate(login, login)
        response = self.url_open(f'/calendar/cancel/{event.access_token}?partner_id={partner.id}&withdraw=1',
                                 allow_redirects=False)
        self.env.invalidate_all()
        return response

    def test_withdraw_pending_request_frees_the_hour(self):
        """Wrong day booked: the family withdraws its pending request itself, no teacher step, nothing
        billed, gone from Pending Approval, and the same hour can be booked again."""
        self._publish(next_monday(17))
        self._submit('family_a_portal', next_monday(17), self.student_a2)
        event = self._events(self.student_a2)
        listing = self.url_open('/my/sessions').text
        self.assertIn('Cancel Request', listing)
        self.assertIn(f'/calendar/cancel/{event.access_token}?partner_id={self.family_a.id}&amp;withdraw=1', listing)
        response = self._withdraw('family_a_portal', event, self.family_a)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith('/my/sessions?withdrawn=1'), response.headers['Location'])
        self.assertFalse(event.active)
        self.assertEqual(event.appointment_status, 'cancelled')
        pending = self.env.ref('teaching_course_management.action_teaching_sessions_pending')
        self.assertNotIn(event, self.env['calendar.event'].search(safe_eval(pending.domain)))
        self.assertFalse(event.teaching_invoice_ids)
        with self.assertRaises(UserError):
            event.with_user(self.instructor).action_teaching_bill()
        self._submit('family_a_portal', next_monday(17), self.student_a2)
        self.assertEqual(len(self._events(self.student_a2)), 1, 'the withdrawn hour is free again')

    def test_withdraw_two_hour_request_withdraws_both_hours(self):
        """A 2-hour request is two linked hours: withdrawing either leaves no half-booking."""
        self._publish(next_monday(17), next_monday(18))
        self._submit('family_a_portal', next_monday(17), self.student_a2, two_hours=True)
        first, second = self._events(self.student_a2)
        self.assertEqual(first.teaching_pair_event_id, second)
        self.assertEqual(second.teaching_pair_event_id, first)
        self._withdraw('family_a_portal', second, self.family_a)
        self.assertFalse(first.active or second.active)
        self.assertEqual({first.appointment_status, second.appointment_status}, {'cancelled'})

    def test_withdraw_after_teacher_approved_cancels_nothing(self):
        """Teacher approves while the family's page still shows Cancel Request: the stale click
        cancels nothing; the confirmed-session rules then apply as before."""
        self._publish(next_monday(17))
        self._submit('family_a_portal', next_monday(17), self.student_a2)
        event = self._events(self.student_a2)
        event.with_user(self.instructor).action_teaching_approve()
        response = self._withdraw('family_a_portal', event, self.family_a)
        self.assertTrue(response.headers['Location'].endswith(f'/my/sessions/{event.id}?confirmed_meanwhile=1'))
        self.assertTrue(event.active)
        self.assertEqual(event.appointment_status, 'booked')
        page = self.url_open(f'/my/sessions/{event.id}?confirmed_meanwhile=1').text
        self.assertIn('just confirmed', page)
        self.assertNotIn('Cancel Request', page)

    def test_teacher_cannot_approve_withdrawn_request(self):
        """The other order: a request the family withdrew cannot be approved from a stale list."""
        self._publish(next_monday(17))
        self._submit('family_a_portal', next_monday(17), self.student_a2)
        event = self._events(self.student_a2)
        self._withdraw('family_a_portal', event, self.family_a)
        with self.assertRaises(UserError):
            event.with_user(self.instructor).action_teaching_approve()

    def test_other_family_cannot_withdraw(self):
        """Another family neither sees the request nor gets its cancel link."""
        self._publish(next_monday(17))
        self._submit('family_a_portal', next_monday(17), self.student_a2)
        event = self._events(self.student_a2)
        self.authenticate('family_b_portal', 'family_b_portal')
        self.assertNotIn(event.access_token, self.url_open('/my/sessions').text)
        self.assertEqual(self.url_open(f'/my/sessions/{event.id}').status_code, 404)
        self.assertTrue(event.active)

    def test_confirmed_session_cancel_unchanged(self):
        """Confirmed sessions keep the plain Cancel before the deadline (no Cancel Request)."""
        event = self.make_session(self.student_a1, start=fields.Datetime.now() + timedelta(days=4))
        self.authenticate('family_a_portal', 'family_a_portal')
        page = self.url_open(f'/my/sessions/{event.id}').text
        self.assertNotIn('Cancel Request', page)
        event.invalidate_recordset(['access_token'])  # the page gave the back-office session its token
        response = self.url_open(f'/calendar/cancel/{event.access_token}?partner_id={self.family_a.id}',
                                 allow_redirects=False)
        self.assertTrue(response.headers['Location'].endswith('/my/sessions?cancelled=1'))
        self.assertEqual(event.with_context(active_test=False).appointment_status, 'cancelled')

    # ---------------------------------------------------------------- note 4: teacher edits published availability

    def test_instructor_edits_free_slot(self):
        """Note 4: a staff instructor who did not create the session type moves and removes free slots."""
        slots = self._publish(next_monday(10), next_monday(12))
        slots = slots.with_user(self.instructor)
        slots[0].write({'start_datetime': local_to_utc(next_monday(11)),
                        'end_datetime': local_to_utc(next_monday(11)) + timedelta(hours=1)})
        self.assertEqual(slots[0].start_datetime, local_to_utc(next_monday(11)))
        slots[1].unlink()
        self.assertFalse(slots[1].exists())
        new = self.env['appointment.slot'].with_user(self.instructor).create({
            'appointment_type_id': self.type_one.id, 'slot_type': 'unique',
            'start_datetime': local_to_utc(next_monday(14)), 'end_datetime': local_to_utc(next_monday(15))})
        self.assertIn(new, self.type_one.slot_ids)

    def test_booked_slot_is_protected(self):
        """Note 4: a slot holding a pending or confirmed booking cannot be moved or removed; the
        booking would be left outside the teacher's availability without anyone noticing."""
        slot = self._publish(next_monday(10))
        event = self.make_session(self.student_a1, start=local_to_utc(next_monday(10)), status='request')
        moved = {'start_datetime': local_to_utc(next_monday(13)), 'end_datetime': local_to_utc(next_monday(14))}
        with self.assertRaises(UserError):
            slot.with_user(self.instructor).write(moved)
        with self.assertRaises(UserError):
            slot.with_user(self.instructor).unlink()
        event.action_teaching_cancel()  # a cancelled booking frees the slot again
        slot.with_user(self.instructor).write(moved)
        self.assertEqual(slot.start_datetime, moved['start_datetime'])

    def test_other_instructor_cannot_edit_slots(self):
        """Note 4: an instructor edits only the types they teach on."""
        other = new_test_user(self.env, login='other_instructor', groups='teaching_course_management.group_teaching_instructor')
        slot = self._publish(next_monday(10))
        with self.assertRaises(AccessError):
            slot.with_user(other).write({'start_datetime': local_to_utc(next_monday(9)),
                                         'end_datetime': local_to_utc(next_monday(10))})

    # ---------------------------------------------------------------- note 6: links that open an error

    def test_instructor_opens_session_of_type_he_does_not_teach(self):
        """Note 6: All Sessions › a Live Course session opened an Access Error for an instructor who is
        not staff on that (unpublished) type. Instructors read every session type, still edit none."""
        other = new_test_user(self.env, login='other_instructor2', groups='teaching_course_management.group_teaching_instructor')
        self.type_course.is_published = False
        self.type_course.with_user(other).read(['name', 'teaching_is_group', 'appointment_tz', 'teaching_product_id'])
        with self.assertRaises(AccessError):
            self.type_course.with_user(other).write({'name': 'Renamed'})

    def test_announcements_row_opens_the_post_form(self):
        """Note 6: Teaching › Configuration › Announcements opened the public website page on a row click
        (Odoo's Website pages list). Its own list opens the post's form."""
        action = self.env.ref('teaching_course_management.action_teaching_announcements')
        list_view = action.view_ids.filtered(lambda v: v.view_mode == 'list').view_id
        self.assertEqual(list_view, self.env.ref('teaching_course_management.blog_post_view_list_teaching'))
        arch = self.env['blog.post'].get_views([(list_view.id, 'list')])['views']['list']['arch']
        self.assertNotIn('open_website_url', arch)

    def test_no_generic_appointment_pages_for_families(self):
        """Notes 1 and 6: Odoo's own Appointments list, its breadcrumb on the session page and the
        booking-email link all led a family to the generic "We will come back to you to confirm it"
        page. Teaching sessions now stay in My Sessions."""
        self._publish(next_monday(17))
        self._submit('family_a_portal', next_monday(17), self.student_a2)
        event = self._events(self.student_a2)
        session_page = self.url_open(f'/my/sessions/{event.id}').text
        self.assertNotIn('/my/appointments', session_page)
        self.assertNotIn(event.name, self.url_open('/my/appointments').text)
        response = self.url_open(f'/calendar/view/{event.access_token}?partner_id={self.family_a.id}', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith(f'/my/sessions/{event.id}'), response.headers['Location'])

    # ---------------------------------------------------------------- note 5: Upcoming Sessions

    def test_new_booking_in_upcoming_immediately(self):
        """Note 5: a booking shows in Upcoming at once, as Pending Approval, for the student and the guardian."""
        self._publish(next_monday(17), next_monday(18))
        self._submit('student_a1_portal', next_monday(17), two_hours=True)
        events = self._events(self.student_a1)
        self.assertEqual(len(events), 2)
        for login in ('student_a1_portal', 'family_a_portal'):
            self.authenticate(login, login)
            page = self.url_open('/my/sessions').text
            upcoming = page.split('Past')[0]
            for event in events:
                self.assertIn(f'/my/sessions/{event.id}', upcoming)
            self.assertIn('Pending Approval', upcoming)
        self.authenticate('family_b_portal', 'family_b_portal')
        page = self.url_open('/my/sessions').text
        for event in events:
            self.assertNotIn(f'/my/sessions/{event.id}', page)
        events.action_teaching_approve()
        self.authenticate('student_a1_portal', 'student_a1_portal')
        self.assertIn('Confirmed', self.url_open('/my/sessions').text)

    # ---------------------------------------------------------------- note 6: links

    def test_announcement_view_course_targets_the_course(self):
        """Note 6: "View course" on an announcement opens that course, not just the course list."""
        course = self.env['teaching.course'].create({
            'name': 'Linked course', 'subject_id': self.subject.id, 'curriculum_id': self.curriculum.id,
            'grade_id': self.grade.id, 'wed': True})
        post = self.env['blog.post'].browse(course.action_publish_announcement()['res_id'])
        self.assertIn(f'/my/courses#course-{course.id}', self.url_open(post.website_url).text)

    def test_no_working_hours_menu(self):
        """Note 6: flexible availability ignores working hours (SARI-11), so My Schedule has no
        Working Hours entry that opened the company's employee list."""
        self.assertFalse(self.env.ref('teaching_course_management.menu_teaching_working_hours', raise_if_not_found=False))
