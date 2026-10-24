from werkzeug.exceptions import NotFound

from odoo.http import request, route

from odoo.addons.appointment.controllers.calendar import AppointmentCalendarController


class TeachingCalendarController(AppointmentCalendarController):

    def _get_prevent_cancel_status(self, event):
        """R6: see calendar.event._teaching_portal_can_cancel. Anything else (late cancelled,
        attended, a course or shared session) cannot be cancelled with the access token."""
        if event.is_teaching and not event._teaching_portal_can_cancel():
            return 'no_time_left'
        return super()._get_prevent_cancel_status(event)

    @route()
    def appointment_cancel(self, access_token, partner_id=False, **kwargs):
        """Sari 10 Oct: a family cancelling from its session page returns to My Sessions, not to
        the booking calendar of that session type.
        Sari 23 Oct: a family withdraws its own pending request (wrong day booked) without the
        teacher. `withdraw=1` means the family clicked "Cancel Request": if the teacher approved
        it meanwhile, nothing is cancelled. The other hour of a 2-hour request goes with it."""
        event = request.env['calendar.event'].sudo().search([('access_token', '=', access_token)], limit=1)
        pair = request.env['calendar.event']
        if event.is_teaching:
            # Serialise with the teacher approving the same request, then re-read the status.
            locked = event | event.teaching_pair_event_id
            request.env.cr.execute('SELECT id FROM calendar_event WHERE id IN %s FOR UPDATE', [tuple(locked.ids)])
            locked.invalidate_recordset(['appointment_status', 'active'])
            if kwargs.get('withdraw') and event.appointment_status != 'request':
                return request.redirect('/my/sessions/%s?confirmed_meanwhile=1' % event.id)
            if event.appointment_status == 'request':
                pair = event.teaching_pair_event_id.filtered(lambda e: e.active and e.appointment_status == 'request')
        was_request = event.appointment_status == 'request'
        response = super().appointment_cancel(access_token, partner_id=partner_id, **kwargs)
        if event.is_teaching and not event.active and not request.env.user._is_public():
            for other in pair:
                other.action_cancel_meeting([int(partner_id)] if partner_id else [])
            return request.redirect('/my/sessions?%s=1' % ('withdrawn' if was_request else 'cancelled'))
        return response

    @route()
    def appointment_view(self, access_token, partner_id=False, state=False, attendee_token=None, **kwargs):
        """Sari 10 Oct: a signed-in family opening a session link (booking email) gets its own session
        page, not the generic "We will come back to you to confirm it" page. Others keep the native page."""
        event = request.env['calendar.event'].sudo().search([('access_token', '=', access_token)], limit=1)
        if event.is_teaching and event.active and not request.env.user._is_public() and (
                event.teaching_student_ids & request.env.user.partner_id.sudo()._teaching_family_students()):
            return request.redirect('/my/sessions/%s' % event.id)
        return super().appointment_view(access_token, partner_id=partner_id, state=state, attendee_token=attendee_token, **kwargs)

    @route()
    def calendar_join_videocall(self, access_token):
        """No meeting before the academy confirms the booking (the token also travels in the
        booking email)."""
        event = request.env['calendar.event'].sudo().search([('access_token', '=', access_token)], limit=1)
        if event.is_teaching and event.appointment_status == 'request':
            raise NotFound()
        return super().calendar_join_videocall(access_token)

    @route()
    def calendar_videocall(self, access_token):
        event = request.env['calendar.event'].sudo().search([('access_token', '=', access_token)], limit=1)
        if event.is_teaching and event.appointment_status == 'request':
            raise NotFound()
        return super().calendar_videocall(access_token)
