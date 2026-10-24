import json
import uuid
from urllib.parse import quote

from werkzeug.exceptions import NotFound

from odoo import fields, http
from odoo.http import request
from odoo.tools import format_amount

from odoo.addons.appointment.controllers.portal import AppointmentPortal


class TeachingPortal(AppointmentPortal):
    """Student / guardian pages. Portal users have no ACL on these models: every read is
    sudo() narrowed to the user's own students, and every record route re-checks that scope."""

    def _get_portal_default_domain(self):
        # Sari 10 Oct: teaching sessions live in My Sessions only. Odoo's own Appointments list
        # opened them on the generic "We will come back to you to confirm it" page.
        return super()._get_portal_default_domain() + [('is_teaching', '=', False)]

    def _teaching_students(self, student=None):
        # Same family scope as the booking form: the user itself if a student, else its student children.
        students = request.env.user.partner_id.sudo()._teaching_family_students()
        if student:
            students = students.filtered(lambda s: str(s.id) == str(student)) or students
        return students

    def _teaching_session_domain(self, students):
        return [('is_teaching', '=', True), ('teaching_student_ids', 'in', students.ids)]

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if not counters:  # the page itself, not the /my/counters RPC
            values['teaching_balance_due'] = format_amount(
                request.env, request.env.user.partner_id.commercial_partner_id.sudo().credit, request.env.company.currency_id)
        if 'teaching_session_count' in counters or 'teaching_course_count' in counters:
            students = self._teaching_students()
            if 'teaching_session_count' in counters:
                values['teaching_session_count'] = request.env['calendar.event'].sudo().search_count(
                    self._teaching_session_domain(students)) if students else 0
            if 'teaching_course_count' in counters:
                values['teaching_course_count'] = request.env['teaching.course'].sudo().search_count(
                    [('student_ids', 'in', students.ids)]) if students else 0
        return values

    def _prepare_portal_layout_values(self):
        values = super()._prepare_portal_layout_values()
        blog = request.env.ref('teaching_course_management.blog_announcements', raise_if_not_found=False)
        values['teaching_announcements_url'] = '/blog/%s' % request.env['ir.http']._slug(blog.sudo()) if blog else '/blog'
        # Sari 10 Oct: "Book a Session" lists the teaching session types only, each with its own calendar
        types = request.env['appointment.type'].sudo().search(
            [('teaching_product_id', '!=', False), ('is_published', '=', True)])
        values['teaching_book_url'] = '/appointment?filter_appointment_type_ids=%s' % quote(json.dumps(types.ids))
        return values

    def _teaching_common_values(self, student=None):
        students = self._teaching_students()
        selected = self._teaching_students(student) if student else students
        return {
            'teaching_students': students,
            'teaching_selected': selected if student else False,
        }

    @http.route(['/my/sessions'], type='http', auth='user', website=True)
    def portal_my_sessions(self, student=None, **kw):
        values = self._prepare_portal_layout_values()
        values.update(self._teaching_common_values(student))
        scope = values['teaching_selected'] or values['teaching_students']
        sessions = request.env['calendar.event'].sudo().search(
            self._teaching_session_domain(scope), order='start') if scope else request.env['calendar.event']
        now = fields.Datetime.now()
        values.update({
            'page_name': 'teaching_sessions',
            'cancelled': kw.get('cancelled'),
            'withdrawn': kw.get('withdrawn'),
            'partner_id': request.env.user.partner_id.id,
            'upcoming': sessions.filtered(lambda e: e.stop >= now),
            'past': sessions.filtered(lambda e: e.stop < now).sorted('start', reverse=True),
        })
        return request.render('teaching_course_management.portal_my_sessions', values)

    @http.route(['/my/sessions/<int:event_id>'], type='http', auth='user', website=True)
    def portal_my_session(self, event_id, **kw):
        students = self._teaching_students()
        event = request.env['calendar.event'].sudo().with_context(active_test=False).search(
            self._teaching_session_domain(students) + [('id', '=', event_id)], limit=1) if students else None
        if not event:
            raise NotFound()
        if not event.access_token:  # back-office events have none; the native cancel route needs it
            event.access_token = uuid.uuid4().hex
        attachments = request.env['ir.attachment'].sudo().search(
            [('res_model', '=', 'calendar.event'), ('res_id', '=', event.id), ('mimetype', '!=', 'text/calendar')])
        attachments -= request.env['mail.message'].sudo().search([  # files of internal log notes stay internal
            ('model', '=', 'calendar.event'), ('res_id', '=', event.id), ('subtype_id.internal', '=', True)]).attachment_ids
        for attachment in attachments:
            attachment.generate_access_token()
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'teaching_session',
            'booked': kw.get('booked'),
            'confirmed_meanwhile': kw.get('confirmed_meanwhile'),
            'event': event,
            'attachments': attachments,
            'can_cancel': event._teaching_portal_can_cancel(),
            'show_link': event.appointment_status == 'booked' and event.teaching_mode == 'online' and event.videocall_location,
            'partner_id': request.env.user.partner_id.id,
        })
        return request.render('teaching_course_management.portal_my_session', values)

    @http.route(['/my/courses'], type='http', auth='user', website=True)
    def portal_my_courses(self, student=None, **kw):
        values = self._prepare_portal_layout_values()
        values.update(self._teaching_common_values(student))
        scope = values['teaching_selected'] or values['teaching_students']
        courses = request.env['teaching.course'].sudo().search(
            [('student_ids', 'in', scope.ids)]) if scope else request.env['teaching.course']
        values.update({'page_name': 'teaching_courses', 'courses': courses, 'scope': scope})
        return request.render('teaching_course_management.portal_my_courses', values)

    @http.route(['/my/balance'], type='http', auth='user', website=True)
    def portal_my_balance(self, **kw):
        commercial = request.env.user.partner_id.commercial_partner_id.sudo()
        invoices = request.env['account.move'].sudo().search([
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            ('state', '=', 'posted'),
            ('commercial_partner_id', '=', commercial.id),
        ], order='invoice_date desc, id desc')
        payments = request.env['account.payment'].sudo().search([
            ('partner_id', 'child_of', commercial.id),
            ('payment_type', '=', 'inbound'),
            ('state', 'in', ('in_process', 'paid')),
        ], order='date desc, id desc')
        invoiced = sum(invoices.mapped('amount_total_signed'))
        outstanding = sum(invoices.mapped('amount_residual_signed'))
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'teaching_balance',
            'currency': request.env.company.currency_id,
            'invoiced': invoiced,
            'outstanding': outstanding,
            'paid': invoiced - outstanding,
            'balance_due': commercial.credit,
            'unpaid_invoices': invoices.filtered(lambda m: m.payment_state in ('not_paid', 'partial')),
            'payments': payments,
        })
        return request.render('teaching_course_management.portal_my_balance', values)
