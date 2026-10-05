from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

from .common import TeachingCommonMixin


@tagged('post_install', '-at_install', 'teaching')
class TestTeachingPortal(TeachingCommonMixin, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_teaching()
        cls.portal_family_a = new_test_user(cls.env, login='family_a_portal', groups='base.group_portal',
                                            partner_id=cls.family_a.id)
        cls.portal_student_a1 = new_test_user(cls.env, login='student_a1_portal', groups='base.group_portal',
                                              partner_id=cls.student_a1.id)
        start = fields.Datetime.now() + timedelta(days=3)
        cls.session_a1 = cls.make_session(cls.student_a1, start=start)
        cls.session_a2 = cls.make_session(cls.student_a2, start=start + timedelta(hours=2))
        cls.session_b1 = cls.make_session(cls.student_b1, start=start + timedelta(hours=4))

    def test_guardian_sees_own_children_only(self):
        """W29: a guardian sees every child's sessions, never another family's."""
        self.authenticate('family_a_portal', 'family_a_portal')
        page = self.url_open('/my/sessions').text
        self.assertIn(f'/my/sessions/{self.session_a1.id}', page)
        self.assertIn(f'/my/sessions/{self.session_a2.id}', page)
        self.assertNotIn(f'/my/sessions/{self.session_b1.id}', page)
        self.assertEqual(self.url_open(f'/my/sessions/{self.session_b1.id}').status_code, 404)
        self.assertEqual(self.url_open(f'/my/sessions/{self.session_a2.id}').status_code, 200)

    def test_student_sees_own_sessions_only(self):
        """W28: a student sees their own sessions, not a sibling's."""
        self.authenticate('student_a1_portal', 'student_a1_portal')
        page = self.url_open('/my/sessions').text
        self.assertIn(f'/my/sessions/{self.session_a1.id}', page)
        self.assertNotIn(f'/my/sessions/{self.session_a2.id}', page)
        self.assertEqual(self.url_open(f'/my/sessions/{self.session_a2.id}').status_code, 404)

    def test_meeting_link_hidden_while_pending(self):
        """The meeting link is shown only once the academy confirms the booking."""
        pending = self.make_session(self.student_a1, start=fields.Datetime.now() + timedelta(days=5), status='request')
        pending.videocall_location = 'https://meet.example.com/sigma-test'
        self.authenticate('family_a_portal', 'family_a_portal')
        self.assertNotIn('meet.example.com/sigma-test', self.url_open(f'/my/sessions/{pending.id}').text)
        pending.action_teaching_approve()
        self.assertIn('meet.example.com/sigma-test', self.url_open(f'/my/sessions/{pending.id}').text)

    def test_balance_matches_accounting(self):
        """W17 / Sari 29 Sep: Balance shows invoiced, paid and outstanding from posted invoices and payments."""
        self.session_a1.action_set_appointment_attended()
        self.session_a1.teaching_actual_duration = 1.5
        self.session_a1.action_teaching_bill()
        invoice = self.session_a1.teaching_invoice_ids
        bank = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.env.company.id)], limit=1)
        self.env['account.payment.register'].with_context(active_model='account.move', active_ids=invoice.ids).create(
            {'journal_id': bank.id, 'amount': 100})._create_payments()
        self.authenticate('family_a_portal', 'family_a_portal')
        page = self.url_open('/my/balance').text
        for amount in ('225.00', '100.00', '125.00'):
            self.assertIn(amount, page)
        self.assertIn(invoice.name, page)

    def test_home_and_courses_pages(self):
        self.authenticate('family_a_portal', 'family_a_portal')
        self.assertEqual(self.url_open('/my').status_code, 200)
        self.assertEqual(self.url_open('/my/courses').status_code, 200)
        self.assertIn('Announcements', self.url_open('/my').text)
