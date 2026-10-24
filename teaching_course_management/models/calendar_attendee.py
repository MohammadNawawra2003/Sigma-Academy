from odoo import _, fields, models

# Odoo's attendee emails, by what they tell the family
NOTICE_KIND = {
    'calendar.calendar_template_meeting_invitation': 'invitation',
    'appointment.appointment_booked_mail_template': 'invitation',
    'appointment.attendee_invitation_mail_template': 'invitation',
    'calendar.calendar_template_meeting_changedate': 'update',
    'calendar.calendar_template_meeting_update': 'update',
    'calendar.calendar_template_meeting_reminder': 'reminder',
    'calendar.calendar_template_delete_event': 'cancel',
    'appointment.appointment_canceled_mail_template': 'cancel',
}


class CalendarAttendee(models.Model):
    _inherit = 'calendar.attendee'

    def _teaching_private(self):
        """Family attendees of a session shared by several families. Odoo's attendee emails list
        every attendee and attach an .ics with every address: they get the teaching notice instead."""
        return self.filtered(lambda a: a.event_id._teaching_is_shared() and not a.partner_id.user_ids.filtered(
            lambda u: not u.share))

    def _teaching_notice_heading(self):
        return {
            'invitation': _('You are booked on this session'),
            'reminder': _('Reminder: your session is coming up'),
            'cancel': _('This session was cancelled'),
        }.get(self.env.context.get('teaching_notice'), _('Your session has changed'))

    def _teaching_send_notice(self, kind, force_send=True):
        template = self.env.ref('teaching_course_management.mail_template_shared_session_notice')
        return self.with_context(teaching_notice=kind)._notify_attendees(template, notify_author=True, force_send=force_send)

    def _send_invitation_emails(self):
        # appointment's override calls calendar's _notify_attendees directly, past ours: filter here too
        private = self._teaching_private()
        now = fields.Datetime.now()
        private.filtered(lambda a: a.event_id.start > now)._teaching_send_notice('invitation')
        return super(CalendarAttendee, self - private)._send_invitation_emails()

    def _notify_attendees(self, mail_template, notify_author=False, force_send=False):
        private = self.browse() if self.env.context.get('teaching_notice') else self._teaching_private()
        if private:
            kind = NOTICE_KIND.get(mail_template.get_external_id().get(mail_template.id), 'update') if mail_template else 'update'
            private._teaching_send_notice(kind, force_send=force_send)
        return super(CalendarAttendee, self - private)._notify_attendees(mail_template, notify_author=notify_author, force_send=force_send)
