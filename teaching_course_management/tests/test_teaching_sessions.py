from datetime import datetime, timedelta

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from .common import TeachingCommonMixin, local_to_utc


@tagged('post_install', '-at_install', 'teaching')
class TestTeachingSessions(TeachingCommonMixin, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setup_teaching()

    def _register_payment(self, moves, amount=None):
        bank = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.env.company.id)], limit=1)
        vals = {'journal_id': bank.id}
        if amount is not None:
            vals['amount'] = amount
        return self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=moves.ids).create(vals)._create_payments()

    def test_guardian_flag_and_attendees(self):
        """A contact with a student child is a guardian; students and guardians become attendees."""
        self.assertTrue(self.family_a.is_guardian)
        self.assertFalse(self.student_a1.is_guardian)
        event = self.make_session(self.student_a1)
        self.assertTrue(event.is_teaching)
        self.assertIn(self.student_a1, event.partner_ids)
        self.assertIn(self.family_a, event.partner_ids)

    def test_back_office_booked_website_request(self):
        """R2: a back-office booking is Confirmed; a website booking of a manual-confirm type is a request."""
        event = self.env['calendar.event'].with_user(self.instructor).create({
            'name': 'Direct', 'user_id': self.instructor.id, 'appointment_type_id': self.type_one.id,
            'start': datetime.now() + timedelta(days=2), 'stop': datetime.now() + timedelta(days=2, hours=1),
            'teaching_student_ids': [Command.set(self.student_a1.ids)]})
        self.assertEqual(event.appointment_status, 'booked')
        start = datetime.now() + timedelta(days=2)
        self.assertEqual(self.type_one._get_default_appointment_status(start, start + timedelta(hours=1), 1), 'request')

    def test_approve_and_reject(self):
        """Approve confirms and notifies; Reject needs a reason, notifies it and frees the slot (archived)."""
        request = self.make_session(self.student_a1, status='request')
        request.with_user(self.instructor).action_teaching_approve()
        self.assertEqual(request.appointment_status, 'booked')
        confirmation = self.env['mail.message'].search(
            [('model', '=', 'calendar.event'), ('res_id', '=', request.id), ('message_type', '=', 'comment')])
        self.assertIn('confirmed', confirmation.body)
        self.assertIn(self.family_a, confirmation.partner_ids, 'the guardian receives the confirmation')
        with self.assertRaises(UserError):
            request.with_user(self.instructor).action_teaching_approve()

        to_reject = self.make_session(self.student_a2, status='request', start=request.stop + timedelta(hours=1))
        action = to_reject.with_user(self.instructor).action_teaching_reject()
        wizard = self.env[action['res_model']].with_user(self.instructor).with_context(action['context']).create(
            {'reason': 'Not available that day'})
        wizard.action_confirm()
        self.assertEqual(to_reject.appointment_status, 'cancelled')
        self.assertFalse(to_reject.active)
        self.assertTrue(self.env['mail.message'].search_count([
            ('model', '=', 'calendar.event'), ('res_id', '=', to_reject.id), ('body', 'ilike', 'Not available that day')]))

    def test_bill_guards(self):
        """R7: no invoice before the session is attended and the actual duration confirmed."""
        event = self.make_session(self.student_a1)
        with self.assertRaises(UserError):
            event.with_user(self.instructor).action_teaching_bill()
        event.with_user(self.instructor).action_set_appointment_attended()
        self.assertEqual(event.teaching_actual_duration, 1.0, 'Mark Attended pre-fills the planned duration')
        event.teaching_actual_duration = 0
        with self.assertRaises(UserError):
            event.with_user(self.instructor).action_teaching_bill()
        self.assertFalse(event.teaching_invoice_ids)
        with self.assertRaises(ValidationError):
            event.teaching_actual_duration = -1

    def test_bill_amount_guardian_idempotent(self):
        """1:30 × 150 = 225, zero tax, billed to the guardian, once — repeated clicks never duplicate."""
        event = self.make_session(self.student_a1)
        event.with_user(self.instructor).action_set_appointment_attended()
        event.teaching_actual_duration = 1.5
        event.with_user(self.instructor).action_teaching_bill()
        invoice = event.teaching_invoice_ids
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.partner_id, self.family_a)
        self.assertEqual(invoice.teaching_student_id, self.student_a1)
        self.assertAlmostEqual(invoice.amount_total, 225.0)
        self.assertAlmostEqual(invoice.amount_tax, 0.0)
        self.assertFalse(invoice.invoice_line_ids.tax_ids)
        self.assertTrue(event.teaching_duration_confirmed)
        self.assertEqual(invoice.teaching_event_ids, event)
        event.with_user(self.instructor).action_teaching_bill()
        self.assertEqual(event.teaching_invoice_ids, invoice)
        self.assertEqual(self.env['account.move'].search_count([('teaching_student_id', '=', self.student_a1.id)]), 1)
        with self.assertRaises(UserError):
            event.teaching_actual_duration = 2.0
        with self.assertRaises(UserError):
            event.appointment_status = 'booked'

    def test_minutes_billing_and_invoice_to_student(self):
        """Duration in minutes bills exactly (1:20 = 200); Invoice To = Student bills the student."""
        event = self.make_session(self.student_b1)
        event.action_set_appointment_attended()
        event.teaching_actual_duration = 80 / 60
        event.action_teaching_bill()
        self.assertEqual(event.teaching_invoice_ids.partner_id, self.student_b1)
        self.assertAlmostEqual(event.teaching_invoice_ids.amount_total, 200.0, places=1)

    def test_convert_to_group_no_credit_note(self):
        """R9: converting a confirmed 1-to-1 bills every student at the group rate, without credit note."""
        event = self.make_session(self.student_a1)
        with self.assertRaises(ValidationError):
            event.teaching_student_ids = [Command.link(self.student_a2.id)]
        action = event.with_user(self.instructor).action_teaching_convert_group()
        self.env[action['res_model']].with_user(self.instructor).with_context(action['context']).create(
            {'student_ids': [Command.set(self.student_a2.ids)]}).action_confirm()
        self.assertEqual(event.appointment_type_id, self.type_group)
        self.assertEqual(event.teaching_price_unit, 100.0)
        event.action_set_appointment_attended()
        event.action_teaching_bill()
        self.assertEqual(len(event.teaching_invoice_ids), 2)
        self.assertEqual(event.teaching_invoice_ids.mapped('amount_total'), [100.0, 100.0])
        self.assertFalse(self.env['account.move'].search_count([('move_type', '=', 'out_refund')]))

    def test_waive(self):
        """A waived no-show creates no invoice and cannot be billed afterwards."""
        event = self.make_session(self.student_a1)
        event.action_set_appointment_no_show()
        self.assertEqual(event.teaching_amount, 150.0, 'A no-show is billable at the planned duration')
        event.action_teaching_waive()
        self.assertEqual(event.teaching_amount, 0.0)
        self.assertTrue(event.teaching_duration_confirmed)
        with self.assertRaises(UserError):
            event.action_teaching_bill()
        self.assertFalse(event.teaching_invoice_ids)

    def test_capacity_and_waitlist(self):
        """R10: a group never goes over capacity; the waitlist is promoted by hand when a seat frees."""
        five = self.student_a1 | self.extra_students[:4]
        event = self.make_session(five, atype=self.type_group)
        self.assertEqual(event.teaching_seats_taken, 5)
        with self.assertRaises(ValidationError):
            event.teaching_student_ids = [Command.link(self.extra_students[4].id)]
        event.teaching_waitlist_ids = [Command.link(self.student_b1.id)]
        with self.assertRaises(UserError):
            event.action_teaching_promote()
        event.teaching_student_ids = [Command.unlink(self.extra_students[0].id)]
        event.action_teaching_promote()
        self.assertIn(self.student_b1, event.teaching_student_ids)
        self.assertFalse(event.teaching_waitlist_ids)

    def test_cancel_deadline(self):
        """R6: free cancellation until midnight the night before, then Late Cancelled (billable)."""
        tomorrow = local_to_utc(datetime.now().replace(hour=17, minute=0, second=0, microsecond=0) + timedelta(days=2))
        early = self.make_session(self.student_a1, start=tomorrow)
        early.action_teaching_cancel()
        self.assertEqual(early.appointment_status, 'cancelled')
        late = self.make_session(self.student_a2, start=datetime.now() + timedelta(hours=1))
        late.action_teaching_cancel()
        self.assertEqual(late.appointment_status, 'late_cancelled')
        self.assertTrue(late.active, 'Late Cancelled stays active so it can be billed')
        late.action_teaching_bill()
        self.assertAlmostEqual(late.teaching_invoice_ids.amount_total, 150.0)

    def test_income_fields(self):
        """R14: Received counts registered payments only; Outstanding is what is still owed."""
        event = self.make_session(self.student_a1)
        event.action_set_appointment_attended()
        event.teaching_actual_duration = 1.5
        event.action_teaching_bill()
        invoice = event.teaching_invoice_ids
        self.assertEqual(invoice.teaching_amount_received, 0.0)
        self._register_payment(invoice, amount=100)
        self.assertEqual(invoice.payment_state, 'partial')
        self.assertAlmostEqual(invoice.teaching_amount_received, 100.0)
        self.assertAlmostEqual(invoice.amount_residual_signed, 125.0)
        self.assertAlmostEqual(invoice.teaching_amount_received + invoice.amount_residual_signed, invoice.amount_total_signed)
        self.assertAlmostEqual(self.family_a.credit, 125.0)

    def test_reminders_email_only(self):
        """W11: session types remind by email (6 h before); no paid SMS reminder sneaks in from Odoo's defaults."""
        for atype in self.type_one | self.type_group | self.type_review | self.type_course:
            self.assertEqual(atype.reminder_ids, self.env.ref('calendar.alarm_mail_2'))
        self.assertNotIn('sms', self.make_session(self.student_a1).alarm_ids.mapped('alarm_type'))

    def test_followup_monthly_no_cron(self):
        """R13: an automatic 30-day statement level exists; the module adds no cron of its own."""
        level = self.env['account_followup.followup.line'].search(
            [('company_id', '=', self.env.company.id), ('delay', '=', 30)])
        self.assertTrue(level.auto_execute)
        self.assertFalse(self.env['ir.model.data'].search_count(
            [('module', '=', 'teaching_course_management'), ('model', '=', 'ir.cron')]))

    def test_invoiced_session_is_locked(self):
        """Billing integrity: an invoiced session cannot lose its invoice link (rebill), be archived
        (cancelled) or deleted."""
        event = self.make_session(self.student_a1)
        event.action_set_appointment_attended()
        event.action_teaching_bill()
        for vals in ({'teaching_invoice_ids': [Command.clear()]}, {'teaching_duration_confirmed': False}, {'active': False}):
            with self.assertRaises(UserError):
                event.write(vals)
        with self.assertRaises(UserError):
            event.action_archive()
        with self.assertRaises(UserError):
            event.unlink()
        self.assertEqual(len(event.teaching_invoice_ids), 1)

    def test_teaching_menus_open(self):
        """W30/W31: every Teaching menu an instructor or the academy admin sees opens a window that
        user may use (a visible menu ending in "Access Error" or a blank page is a broken button)."""
        admin = new_test_user(self.env, login='teaching_admin_menus', groups=','.join([
            'teaching_course_management.group_teaching_admin', 'account.group_account_manager']))
        root = self.env.ref('teaching_course_management.menu_teaching_root')
        Menu = self.env['ir.ui.menu']
        for user in (self.instructor, admin):
            visible = Menu.browse(Menu.with_user(user)._visible_menu_ids())
            menus = visible & Menu.search([('id', 'child_of', root.id)])
            self.assertTrue(menus)
            for menu in menus.filtered('action'):
                self.assertNotEqual(menu.action._name, 'ir.actions.server',
                                    '%s runs a server action: opened from a menu it has no records and shows a blank page' % menu.complete_name)
                groups = menu.action.group_ids
                self.assertTrue(not groups or groups & user.all_group_ids,
                                '%s opens %s, restricted to %s' % (menu.complete_name, menu.action.name, groups.mapped('name')))

    def test_student_balance_is_family_balance(self):
        """5.4: a student's Balance and the "Has Outstanding" filter show what the family owes (Odoo books
        the receivable on the family contact, so the student's own credit is always 0)."""
        event = self.make_session(self.student_a1)
        event.action_set_appointment_attended()
        event.action_teaching_bill()
        self.assertAlmostEqual(self.student_a1.teaching_balance, 150.0)
        self.assertAlmostEqual(self.student_a2.teaching_balance, 150.0, msg='siblings share the family balance')
        outstanding = self.env['res.partner'].search([('is_student', '=', True), ('commercial_partner_id.credit', '>', 0)])
        self.assertIn(self.student_a1, outstanding)
        self.assertNotIn(self.student_b1, outstanding)

    def test_income_is_admin_only(self):
        """5.9: Income is for the academy administrator; an instructor cannot open it even by direct URL."""
        action = self.env.ref('teaching_course_management.action_teaching_income')
        self.assertIn(self.env.ref('teaching_course_management.group_teaching_admin'), action.group_ids)
        self.assertNotIn(action.group_ids, self.instructor.all_group_ids)
