from urllib.parse import quote

from dateutil.relativedelta import relativedelta
from werkzeug.exceptions import Forbidden

from odoo import Command, http
from odoo.http import request

from odoo.addons.website_appointment.controllers.appointment import WebsiteAppointment


class TeachingAppointment(WebsiteAppointment):

    @http.route()
    def appointment_type_id_form(self, appointment_type_id, *args, **kwargs):
        """Teaching bookings need a known student: public visitors log in first."""
        appointment_type = request.env['appointment.type'].sudo().browse(int(appointment_type_id)).exists()
        if appointment_type.teaching_product_id and request.env.user._is_public():
            return request.redirect('/web/login?redirect=%s' % quote(request.httprequest.full_path, safe=''))
        return super().appointment_type_id_form(appointment_type_id, *args, **kwargs)

    def _prepare_appointment_type_page_values(self, appointment_type, staff_user_id=False, resource_selected_id=False, **kwargs):
        values = super()._prepare_appointment_type_page_values(appointment_type, staff_user_id, resource_selected_id, **kwargs)
        if appointment_type.teaching_product_id:
            values['max_capacity'] = 1  # one booking = one student; a group seat is counted per student
        return values

    def _get_extra_calendar_event_params(self, **kwargs):
        params = super()._get_extra_calendar_event_params(**kwargs)
        student_id = kwargs.get('teaching_student_id')
        if student_id:
            if request.env.user._is_public():
                raise Forbidden()
            allowed = request.env.user.partner_id.sudo()._teaching_family_students()
            student = allowed.filtered(lambda s: str(s.id) == str(student_id))
            if not student:
                raise Forbidden()
            params['teaching_student_ids'] = [Command.set(student.ids)]
        return params

    def _handle_appointment_form_submission(
        self, appointment_type, date_start, date_end, description, duration, allday,
        answer_input_values, name, customer, appointment_invite, guests=None,
        staff_user=None, asked_capacity=1, booking_line_values=None, extra_calendar_event_params=None,
    ):
        if not appointment_type.teaching_product_id:
            return super()._handle_appointment_form_submission(
                appointment_type, date_start, date_end, description, duration, allday, answer_input_values, name,
                customer, appointment_invite, guests, staff_user, asked_capacity, booking_line_values,
                extra_calendar_event_params)
        if request.env.user._is_public():
            raise Forbidden()
        extra = extra_calendar_event_params or {}
        me = request.env.user.partner_id.sudo()
        if not extra.get('teaching_student_ids'):  # every teaching booking is for a known student
            if not me.is_student:
                raise Forbidden()
            extra['teaching_student_ids'] = [Command.set(me.ids)]
        students = me.browse(extra['teaching_student_ids'][0][2])
        # One booking reserves one seat, whatever "Number of people" was posted.
        asked_capacity = 1
        booking_line_values = [dict(v, capacity_reserved=1, capacity_used=1) for v in booking_line_values or []]
        appt = appointment_type.sudo().with_context(teaching_student_ids=students.ids)
        tz = appointment_type.appointment_tz
        resources = request.env['appointment.resource']
        failed = request.redirect('/appointment/%s?state=failed-staff-user' % appointment_type.id)
        # The native submit only re-checks capacity: re-validate the slot itself, which also
        # applies the published-slot, gap and cut-off rules against crafted POSTs.
        if not appt._check_appointment_is_valid_slot(staff_user, resources, asked_capacity, tz, date_start, duration, allday):
            return failed
        second_slot = False
        if request.params.get('teaching_two_hours') and not appointment_type.teaching_is_group:
            second_slot = appt.slot_ids.filtered(
                lambda s: s.slot_type == 'unique' and s.start_datetime == date_end and not s.allday)[:1]
            if not second_slot or not appt._check_appointment_is_valid_slot(
                    staff_user, resources, asked_capacity, tz, date_end, second_slot.duration, allday):
                return request.redirect('/appointment/%s?state=teaching-two-hours' % appointment_type.id)
        response = super()._handle_appointment_form_submission(
            appointment_type, date_start, date_end, description, duration, allday, answer_input_values, name,
            customer, appointment_invite, guests, staff_user, asked_capacity, booking_line_values, extra)
        if second_slot:
            second_end = date_end + relativedelta(hours=second_slot.duration)
            values = appointment_type._prepare_calendar_event_values(
                asked_capacity, [dict(v) for v in booking_line_values or []], description, second_slot.duration, allday,
                appointment_invite, guests, name, customer, staff_user, date_end, second_end)
            request.env['calendar.event'].with_context(
                mail_notify_author=True, mail_create_nolog=True, mail_create_nosubscribe=True,
                allowed_company_ids=self._get_allowed_companies(staff_user or appointment_type.create_uid).ids,
            ).sudo().with_context(skip_contact_description=True).create({
                'appointment_answer_input_ids': [Command.create(dict(v)) for v in answer_input_values],
                **values,
                **extra,
            })
        return response
