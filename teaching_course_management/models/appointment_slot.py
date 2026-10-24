from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import format_datetime

SLOT_TIME_FIELDS = {'start_datetime', 'end_datetime', 'allday', 'appointment_type_id', 'slot_type'}


class AppointmentSlot(models.Model):
    _inherit = 'appointment.slot'

    def _compute_display_name(self):
        # My Availability calendar: a teaching slot reads as its session type, not as two UTC datetimes.
        super()._compute_display_name()
        for slot in self:
            if slot.slot_type == 'unique' and slot.appointment_type_id.teaching_product_id:
                slot.display_name = slot.appointment_type_id.name

    def _teaching_check_no_booking(self):
        """Sari 10 Oct: the instructor edits published availability freely, but a slot holding a
        pending or confirmed booking stays put: moving or deleting it would leave the session
        outside his availability without anyone noticing. Cancel or move the session first."""
        Event = self.env['calendar.event'].sudo()
        for slot in self:
            if not slot.appointment_type_id.teaching_product_id or slot.slot_type != 'unique' or not slot.start_datetime:
                continue
            count = Event.search_count([
                ('appointment_type_id', '=', slot.appointment_type_id.id),
                ('appointment_status', 'in', ('request', 'booked')),
                ('start', '<', slot.end_datetime),
                ('stop', '>', slot.start_datetime),
            ])
            if count:
                raise UserError(_(
                    'The slot %(slot)s has %(count)s pending or confirmed booking(s). '
                    'Cancel or reschedule the session first, then change the slot.',
                    slot=format_datetime(self.env, slot.start_datetime, tz=slot.appointment_type_id.appointment_tz, dt_format='EEE d MMM y, HH:mm'),
                    count=count))

    def write(self, vals):
        if SLOT_TIME_FIELDS & set(vals):
            self._teaching_check_no_booking()
        return super().write(vals)

    def unlink(self):
        self._teaching_check_no_booking()
        return super().unlink()
