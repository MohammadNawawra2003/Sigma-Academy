from odoo import fields

from odoo.addons.appointment.controllers.calendar import AppointmentCalendarController


class TeachingCalendarController(AppointmentCalendarController):

    def _get_prevent_cancel_status(self, event):
        """R6: a confirmed teaching session can be cancelled from the portal until midnight
        the night before. A pending request can always be withdrawn."""
        if (event.is_teaching and event.appointment_status == 'booked'
                and fields.Datetime.now() >= event.teaching_cancel_deadline):
            return 'no_time_left'
        return super()._get_prevent_cancel_status(event)
