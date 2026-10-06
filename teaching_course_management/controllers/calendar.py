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
