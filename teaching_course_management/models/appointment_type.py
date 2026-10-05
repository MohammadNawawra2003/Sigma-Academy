from datetime import timedelta

import pytz

from odoo import api, fields, models

PARAM_GAP = 'teaching_course_management.gap_minutes'
PARAM_CUTOFF = 'teaching_course_management.cutoff_hour'
PARAM_GROUP_CAPACITY = 'teaching_course_management.group_capacity'


class AppointmentType(models.Model):
    _inherit = 'appointment.type'

    teaching_product_id = fields.Many2one(
        'product.product', 'Teaching Product', domain=[('type', '=', 'service')],
        help='Service product (UoM Hours) that prices this session type. Setting it makes the type a teaching session type.')
    teaching_price = fields.Float(
        'Price per Hour', related='teaching_product_id.lst_price', readonly=False, digits='Product Price')
    teaching_is_group = fields.Boolean(
        'Group Session', help='Group, Review and Live Course types: several students, capacity and waitlist apply.')

    def _teaching_capacity(self):
        self.ensure_one()
        if self.manage_capacity:
            return self.user_capacity
        if self.teaching_is_group:
            return int(self.env['ir.config_parameter'].sudo().get_param(PARAM_GROUP_CAPACITY, 5))
        return 1

    @api.model
    def _teaching_booker_students(self):
        """Students the current website user books for: the ones passed by the booking
        controller, else the user's own family (itself if student, else its student children)."""
        if 'teaching_student_ids' in self.env.context:
            return self.env['res.partner'].sudo().browse(self.env.context['teaching_student_ids'])
        if self.env.user._is_public():
            return self.env['res.partner']
        return self.env.user.partner_id.sudo()._teaching_family_students()

    def _teaching_slot_blocked(self, staff_user, start, stop, students):
        """Sigma rules R3/R4 for a website slot. Returns 'cutoff', 'gap' or False.

        - cut-off: a slot starting today (appointment timezone) is closed once the clock passes
          the configured hour (default 12:00);
        - gap: another student's session of the same instructor ending less than N minutes
          before the slot, or starting less than N minutes after it, blocks the slot. Sessions
          whose students are all among ``students`` are exempt (back to back for the same student).
        Back-office bookings never come through here, which is the intended override.
        """
        self.ensure_one()
        if not self.teaching_product_id:
            return False
        icp = self.env['ir.config_parameter'].sudo()
        tz = pytz.timezone(self.appointment_tz or 'UTC')
        now_local = pytz.utc.localize(fields.Datetime.now()).astimezone(tz)
        start_local = pytz.utc.localize(start).astimezone(tz)
        cutoff = float(icp.get_param(PARAM_CUTOFF, 12.0))
        if start_local.date() == now_local.date() and now_local.hour + now_local.minute / 60.0 >= cutoff:
            return 'cutoff'
        gap = timedelta(minutes=int(icp.get_param(PARAM_GAP, 15)))
        if not gap:
            return False
        # ponytail: one search per slot; fine for one instructor's month, batch it if slots grow to hundreds
        neighbours = self.env['calendar.event'].sudo().search([
            ('is_teaching', '=', True),
            ('user_id', '=', staff_user.id),
            ('appointment_status', '!=', 'cancelled'),
            ('start', '<', stop + gap),
            ('stop', '>', start - gap),
        ])
        for event in neighbours:
            if event.start < stop and event.stop > start:
                continue  # a real overlap: the native check decides (group capacity)
            if not event.teaching_student_ids or event.teaching_student_ids - students:
                return 'gap'
        return False

    def _slot_availability_is_user_available(self, slot, staff_user, availability_values, asked_capacity=1):
        available = super()._slot_availability_is_user_available(slot, staff_user, availability_values, asked_capacity)
        if available and self.teaching_product_id:
            start, stop = slot['UTC']
            if self._teaching_slot_blocked(staff_user, start, stop, self._teaching_booker_students()):
                return False
        return available
