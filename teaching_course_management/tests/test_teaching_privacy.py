"""A session shared by several families (group, course, convert to group) shows each family only its
own children: portal pages, every email Odoo or Teaching sends, the .ics file, and the token pages."""
import base64
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

from .common import TeachingCommonMixin


@tagged('post_install', '-at_install', 'teaching')
class TestTeachingSharedSessionPrivacy(TeachingCommonMixin, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_teaching()
        new_test_user(cls.env, login='family_a_portal', groups='base.group_portal', partner_id=cls.family_a.id)
        new_test_user(cls.env, login='family_b_portal', groups='base.group_portal', partner_id=cls.family_b.id)

    def _shared(self, status='booked'):
        event = self.make_session(self.student_a1 | self.student_b1, atype=self.type_group, status=status,
                                  start=fields.Datetime.now() + timedelta(days=4))
        event.name = 'Student A1, Student B1 — Physics Group'  # the back-office name lists every student
        event.access_token = uuid.uuid4().hex
        return event

    def _mails_to(self, partner, event):
        return self.env['mail.message'].search([
            ('model', '=', 'calendar.event'), ('res_id', '=', event.id), ('partner_ids', 'in', partner.ids)])

    def _assert_no_family_a(self, messages):
        self.assertTrue(messages, 'family B got no email at all')
        for message in messages:
            text = '%s %s' % (message.subject or '', message.body)
            for secret in ('Student A1', 'Family A', 'family.a@example.com'):
                self.assertNotIn(secret, text, message.subject)
            for ics in message.attachment_ids.filtered(lambda a: a.mimetype == 'text/calendar'):
                raw = base64.b64decode(ics.datas).decode()
                self.assertNotIn('family.a@example.com', raw)
                self.assertNotIn('Student A1', raw)

    def test_portal_shows_only_own_children(self):
        event = self._shared()
        self.authenticate('family_b_portal', 'family_b_portal')
        for url in ('/my/sessions', f'/my/sessions/{event.id}'):
            page = self.url_open(url).text
            self.assertIn('Student B1', page)
            self.assertNotIn('Student A1', page, url)
            self.assertIn('1 other student', page, url)
        self.authenticate('family_a_portal', 'family_a_portal')
        page = self.url_open(f'/my/sessions/{event.id}').text
        self.assertIn('Student A1', page)
        self.assertNotIn('Student B1', page)

    def test_odoo_mails_hide_other_families(self):
        """Invitation on enrolment, date change and reminder: Odoo's own mails list every attendee
        and attach an .ics with every address. Families get the teaching notice instead."""
        event = self._shared()
        event.start += timedelta(hours=1)
        event.stop += timedelta(hours=1)
        event.attendee_ids._notify_attendees(self.env.ref('calendar.calendar_template_meeting_reminder'))
        self._assert_no_family_a(self._mails_to(self.family_b, event))
        self.assertGreaterEqual(len(self._mails_to(self.family_b, event)), 3, 'invitation, change and reminder')
        self.assertIn('Student B1', self._mails_to(self.family_b, event)[0].body)

    def test_teaching_mails_hide_other_families(self):
        event = self._shared(status='request')
        event.with_user(self.instructor).action_teaching_approve()
        confirmed = self._mails_to(self.family_b, event).filtered(lambda m: 'confirmed' in (m.subject or '').lower())
        self.assertEqual(len(confirmed), 1)
        self.assertIn('Student B1', confirmed.body)
        self._assert_no_family_a(confirmed)

    def test_one_family_keeps_odoo_mails(self):
        """A family's own session (one family, even two children) keeps Odoo's standard emails."""
        event = self.make_session(self.student_a1 | self.student_a2, atype=self.type_group,
                                  start=fields.Datetime.now() + timedelta(days=4))
        mails = self._mails_to(self.family_a, event)
        self.assertTrue(mails)
        self.assertFalse(mails.filtered(lambda m: 'Open My Sessions' in m.body))

    def test_token_pages_send_families_to_their_session_page(self):
        """Odoo's appointment and invitation pages list every attendee: families (signed in or not)
        land on the session page, which checks the family. Staff keep Odoo's pages."""
        event = self._shared()
        attendee = event.attendee_ids.filtered(lambda a: a.partner_id == self.family_b)
        for url in (f'/calendar/view/{event.access_token}', f'/calendar/meeting/view?token={attendee.access_token}&id={event.id}'):
            self.authenticate(None, None)
            response = self.url_open(url, allow_redirects=False)
            self.assertEqual(response.status_code, 303, url)
            self.assertTrue(response.headers['Location'].endswith(f'/my/sessions/{event.id}'), url)
        self.authenticate(self.instructor.login, self.instructor.login)
        response = self.url_open(f'/calendar/view/{event.access_token}', allow_redirects=False)
        self.assertNotIn('/my/sessions/', response.headers.get('Location', ''))

    def test_staff_still_see_every_student(self):
        event = self._shared()
        self.assertEqual(event.with_user(self.instructor).teaching_student_ids, self.student_a1 | self.student_b1)
        self.assertEqual(self._mails_to(self.instructor.partner_id, event).filtered(lambda m: 'Open My Sessions' in m.body),
                         self.env['mail.message'])
