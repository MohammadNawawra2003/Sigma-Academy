from datetime import datetime, time

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

BILLABLE_STATUSES = ('attended', 'no_show', 'late_cancelled')
# Fields frozen once a session has been invoiced: changing them would desync the invoice.
LOCKED_WHEN_INVOICED = {
    'teaching_actual_duration', 'teaching_price_unit', 'teaching_billing',
    'teaching_student_ids', 'appointment_type_id', 'appointment_status',
    'teaching_invoice_ids', 'teaching_duration_confirmed', 'active',
}


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    is_teaching = fields.Boolean(
        'Teaching Session', compute='_compute_is_teaching', store=True, index=True)
    teaching_student_ids = fields.Many2many(
        'res.partner', 'teaching_event_student_rel', 'event_id', 'partner_id', string='Students',
        domain=[('is_student', '=', True)])
    teaching_is_group = fields.Boolean(related='appointment_type_id.teaching_is_group')
    subject_id = fields.Many2one('teaching.subject', 'Subject')
    teaching_mode = fields.Selection([('online', 'Online'), ('onsite', 'On-site')], 'Mode', default='online')
    teaching_course_id = fields.Many2one('teaching.course', 'Course', ondelete='set null', index=True)
    teaching_goal = fields.Text('Session Goal', compute='_compute_teaching_goal')
    teaching_notes = fields.Html('Instructor Notes', help='Visible to the student and guardian in the portal.')
    appointment_status = fields.Selection(
        selection_add=[('late_cancelled', 'Late Cancelled')], ondelete={'late_cancelled': 'set no_show'})
    teaching_cancel_deadline = fields.Datetime('Free Cancellation Until', compute='_compute_teaching_cancel_deadline')
    teaching_actual_duration = fields.Float('Actual Duration', help='May be more or less than planned.')
    teaching_duration_confirmed = fields.Boolean('Duration Confirmed', readonly=True, copy=False)
    teaching_billing = fields.Selection([('bill', 'Bill in full'), ('waive', 'Waive')], 'Billing', default='bill', copy=False)
    teaching_currency_id = fields.Many2one('res.currency', compute='_compute_teaching_currency', store=True)
    teaching_price_unit = fields.Monetary(
        'Price per Hour', currency_field='teaching_currency_id',
        compute='_compute_teaching_price_unit', store=True, readonly=False)
    teaching_amount = fields.Monetary(
        'Amount', currency_field='teaching_currency_id', compute='_compute_teaching_amount', store=True)
    teaching_invoice_ids = fields.Many2many(
        'account.move', 'teaching_event_invoice_rel', 'event_id', 'move_id', string='Invoices', copy=False, readonly=True)
    teaching_invoice_count = fields.Integer(compute='_compute_teaching_invoice_count')
    teaching_waitlist_ids = fields.Many2many(
        'res.partner', 'teaching_event_waitlist_rel', 'event_id', 'partner_id', string='Waitlist',
        domain=[('is_student', '=', True)], copy=False)
    teaching_seats_taken = fields.Integer('Seats Taken', compute='_compute_teaching_seats')
    teaching_capacity = fields.Integer('Capacity', compute='_compute_teaching_seats')

    # ------------------------------------------------------------------ computes

    @api.depends('appointment_type_id.teaching_product_id')
    def _compute_is_teaching(self):
        for event in self:
            event.is_teaching = bool(event.appointment_type_id.teaching_product_id)

    @api.depends('appointment_answer_input_ids.value_text_box')
    def _compute_teaching_goal(self):
        for event in self:
            event.teaching_goal = '\n'.join(filter(None, event.appointment_answer_input_ids.mapped('value_text_box')))

    @api.depends('start', 'appointment_type_id.appointment_tz')
    def _compute_teaching_cancel_deadline(self):
        for event in self:
            if not event.start:
                event.teaching_cancel_deadline = False
                continue
            tz = pytz.timezone(event.appointment_type_id.appointment_tz or event.user_id.tz or 'UTC')
            local_day = pytz.utc.localize(event.start).astimezone(tz).date()
            midnight = tz.localize(datetime.combine(local_day, time.min))
            event.teaching_cancel_deadline = midnight.astimezone(pytz.utc).replace(tzinfo=None)

    @api.depends('appointment_type_id')
    def _compute_teaching_currency(self):  # stored so the Amount can be summed in pivots
        for event in self:
            event.teaching_currency_id = self.env.company.currency_id

    @api.depends('appointment_type_id')
    def _compute_teaching_price_unit(self):
        # Price is taken when the type is set (booking, or Convert to Group), not on every
        # later product price change: a booked session keeps its price.
        for event in self:
            event.teaching_price_unit = event.appointment_type_id.teaching_product_id.lst_price

    @api.depends('teaching_price_unit', 'teaching_actual_duration', 'teaching_student_ids', 'teaching_billing')
    def _compute_teaching_amount(self):
        for event in self:
            event.teaching_amount = 0.0 if event.teaching_billing == 'waive' else (
                event.teaching_price_unit * event.teaching_actual_duration * len(event.teaching_student_ids))

    @api.depends('is_teaching', 'appointment_status')
    def _compute_videocall_redirection(self):
        # The meeting link is shared once the academy confirms: not in the booking email or page.
        super()._compute_videocall_redirection()
        for event in self:
            if event.is_teaching and event.appointment_status == 'request':
                event.videocall_redirection = False

    @api.depends('teaching_invoice_ids')
    def _compute_teaching_invoice_count(self):
        for event in self:
            event.teaching_invoice_count = len(event.teaching_invoice_ids)

    @api.depends('teaching_student_ids', 'appointment_type_id', 'start', 'user_id', 'teaching_course_id.capacity')
    def _compute_teaching_seats(self):
        for event in self:
            event.teaching_capacity = (
                event.teaching_course_id.capacity if event.teaching_course_id
                else event.appointment_type_id._teaching_capacity() if event.appointment_type_id else 0)
            event.teaching_seats_taken = len(event._teaching_same_slot_events().teaching_student_ids)

    def _teaching_same_slot_events(self):
        """Website group bookings create one event per booking on the same slot: seats are
        counted over all active teaching events with the same type, instructor and start."""
        self.ensure_one()
        if not self.id or not self.appointment_type_id or not self.start:
            return self
        return self | self.sudo().search([
            ('is_teaching', '=', True),
            ('appointment_type_id', '=', self.appointment_type_id.id),
            ('user_id', '=', self.user_id.id),
            ('start', '=', self.start),
            ('appointment_status', '!=', 'cancelled'),
        ])

    # ------------------------------------------------------------------ constraints

    @api.constrains('teaching_student_ids', 'appointment_type_id', 'teaching_course_id')
    def _check_teaching_capacity(self):
        for event in self.filtered('is_teaching'):
            if not event.teaching_is_group and not event.teaching_course_id and len(event.teaching_student_ids) > 1:
                raise ValidationError(_('A 1-to-1 session has one student. Use Convert to Group to add more.'))
            if event.teaching_seats_taken > event.teaching_capacity:
                raise ValidationError(_(
                    'The session is full (%(cap)s seats). Add the student to the waitlist instead.',
                    cap=event.teaching_capacity))

    @api.constrains('teaching_actual_duration')
    def _check_teaching_actual_duration(self):
        for event in self:
            if event.teaching_actual_duration < 0 or event.teaching_actual_duration > 12:
                raise ValidationError(_('The actual duration must be between 0:00 and 12:00.'))

    # ------------------------------------------------------------------ ORM

    @api.model_create_multi
    def create(self, vals_list):
        events = super().create(vals_list)
        for event in events.filtered('is_teaching'):
            if not event.teaching_student_ids and event.appointment_booker_id:
                event.teaching_student_ids = event.appointment_booker_id
            if not event.subject_id and event.teaching_course_id:
                event.subject_id = event.teaching_course_id.subject_id
        events.filtered('is_teaching')._teaching_sync_attendees()
        return events

    def write(self, vals):
        locked = LOCKED_WHEN_INVOICED & set(vals)
        if locked and self.filtered('teaching_invoice_ids'):
            raise UserError(_('This session is already invoiced. Its status, students, type, duration and price are locked.'))
        res = super().write(vals)
        if 'teaching_student_ids' in vals:
            self.filtered('is_teaching')._teaching_sync_attendees()
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_invoiced(self):
        if self.filtered('teaching_invoice_ids'):
            raise UserError(_('This session is already invoiced and cannot be deleted.'))

    def _teaching_sync_attendees(self):
        """Students and their guardians are attendees, so they get the invitation, reminders
        and chatter messages."""
        for event in self:
            people = event.teaching_student_ids | event.teaching_student_ids.parent_id
            missing = people - event.partner_ids
            if missing:
                event.partner_ids = [Command.link(p.id) for p in missing]

    # ------------------------------------------------------------------ helpers

    def _teaching_check_status(self, allowed, label):
        for event in self:
            if not event.is_teaching:
                raise UserError(_('This is not a teaching session.'))
            if event.appointment_status not in allowed:
                raise UserError(_('%(action)s is not possible while the session is "%(status)s".',
                                  action=label, status=dict(event._fields['appointment_status']._description_selection(self.env)).get(event.appointment_status)))

    def _teaching_portal_can_cancel(self):
        """R6 for the family's own cancel (portal page and the native /calendar/cancel route): a
        pending request, or a confirmed session before the deadline. Course sessions and sessions
        shared by several students are cancelled by the academy, never by one family."""
        self.ensure_one()
        if self.teaching_course_id or len(self.teaching_student_ids) > 1:
            return False
        return self.appointment_status == 'request' or (
            self.appointment_status == 'booked' and fields.Datetime.now() < self.teaching_cancel_deadline)

    def _teaching_recipients(self):
        return self.teaching_student_ids | self.teaching_student_ids.parent_id

    def _teaching_notify(self, template_xmlid):
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        for event in self:
            if template:
                event.message_post_with_source(
                    template, message_type='comment', subtype_xmlid='mail.mt_comment',
                    partner_ids=event._teaching_recipients().ids)

    # ------------------------------------------------------------------ buttons

    def action_teaching_approve(self):
        self._teaching_check_status(('request',), _('Approve'))
        for event in self:
            event.action_set_appointment_booked()
        self._teaching_notify('teaching_course_management.mail_template_session_confirmed')
        return True

    def action_teaching_reject(self):
        self.ensure_one()
        self._teaching_check_status(('request',), _('Reject'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject Booking'),
            'res_model': 'teaching.session.reject',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_event_id': self.id},
        }

    def _teaching_do_reject(self, reason):
        self._teaching_check_status(('request',), _('Reject'))
        self.with_context(teaching_reject_reason=reason)._teaching_notify(
            'teaching_course_management.mail_template_session_rejected')
        for event in self:
            event.action_set_appointment_cancelled()

    def action_set_appointment_attended(self):
        if self.is_teaching:
            self._teaching_check_status(('booked',), _('Mark Attended'))
            self._teaching_prefill_duration()
        return super().action_set_appointment_attended()

    def action_set_appointment_no_show(self):
        if self.is_teaching:
            self._teaching_check_status(('booked',), _('No Show'))
            self._teaching_prefill_duration()
        return super().action_set_appointment_no_show()

    def _teaching_prefill_duration(self):
        for event in self:
            if not event.teaching_actual_duration:
                event.teaching_actual_duration = event.duration

    def action_teaching_cancel(self):
        """Back-office cancel: free before the deadline, Late Cancelled (billable) after it."""
        self._teaching_check_status(('request', 'booked'), _('Cancel'))
        now = fields.Datetime.now()
        for event in self:
            if event.appointment_status == 'request' or now < event.teaching_cancel_deadline:
                event.action_set_appointment_cancelled()
            else:
                event._teaching_prefill_duration()
                event.appointment_status = 'late_cancelled'
                event.message_post(body=_('Cancelled after the free-cancellation deadline: Late Cancelled (billable).'))
        return True

    def action_teaching_waive(self):
        self._teaching_check_status(('no_show', 'late_cancelled'), _('Waive Charge'))
        for event in self:
            if event.teaching_invoice_ids:
                raise UserError(_('This session is already invoiced.'))
            event.write({'teaching_billing': 'waive', 'teaching_duration_confirmed': True})
            event.message_post(body=_('Charge waived. No invoice will be created.'))
        return True

    def action_teaching_bill(self):
        """Confirm Duration & Bill: one posted invoice per student, zero tax. Idempotent."""
        self.ensure_one()
        # Serialise concurrent clicks on the same session, then re-read the lock field.
        self.env.cr.execute('SELECT id FROM calendar_event WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['teaching_invoice_ids'])
        if self.teaching_invoice_ids:
            return self.action_view_teaching_invoices()
        self._teaching_check_status(BILLABLE_STATUSES, _('Confirm Duration & Bill'))
        if self.teaching_billing != 'bill':
            raise UserError(_('This charge was waived.'))
        if self.teaching_actual_duration <= 0:
            raise UserError(_('Confirm the actual duration first.'))
        if not self.teaching_student_ids:
            raise UserError(_('Add the student(s) first.'))
        product = self.appointment_type_id.teaching_product_id
        moves = self.env['account.move'].create([
            self._teaching_invoice_vals(student, product) for student in self.teaching_student_ids])
        moves.action_post()
        self.write({
            'teaching_invoice_ids': [Command.set(moves.ids)],
            'teaching_duration_confirmed': True,
        })
        self.message_post(body=_('Duration confirmed and invoiced: %s', ', '.join(moves.mapped('name'))))
        if not self.env.context.get('teaching_skip_invoice_send'):  # demo data: no mail to fake addresses
            for move in moves.filtered(lambda m: m.partner_id.email):
                move._generate_and_send(allow_fallback_pdf=True)
        return self.action_view_teaching_invoices()

    def _teaching_invoice_vals(self, student, product):
        partner = student.parent_id if student.teaching_invoice_to == 'guardian' and student.parent_id else student
        tz = pytz.timezone(self.appointment_type_id.appointment_tz or self.user_id.tz or 'UTC')
        local_start = pytz.utc.localize(self.start).astimezone(tz)
        hours, minutes = divmod(round(self.teaching_actual_duration * 60), 60)
        label = ' — '.join(filter(None, [
            ' '.join(filter(None, [self.subject_id.name, self.appointment_type_id.name])),
            ' '.join(filter(None, [student.name, student.grade_id.name, student.curriculum_id.name])),
            local_start.strftime('%d/%m/%Y %H:%M'),
            '%d:%02d' % (hours, minutes),
        ]))
        return {
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': fields.Date.context_today(self),
            'teaching_student_id': student.id,
            'invoice_line_ids': [Command.create({
                'product_id': product.id,
                'name': label,
                'quantity': self.teaching_actual_duration,
                'product_uom_id': product.uom_id.id,
                'price_unit': self.teaching_price_unit,
                'tax_ids': [Command.clear()],
            })],
        }

    def action_view_teaching_invoices(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_out_invoice_type')
        action['domain'] = [('id', 'in', self.teaching_invoice_ids.ids)]
        action['context'] = {'default_move_type': 'out_invoice', 'create': False}
        if len(self.teaching_invoice_ids) == 1:
            action.update(views=[(False, 'form')], res_id=self.teaching_invoice_ids.id)
        return action

    def action_view_teaching_course(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'teaching.course',
                'res_id': self.teaching_course_id.id, 'view_mode': 'form'}

    def action_teaching_convert_group(self):
        self.ensure_one()
        self._teaching_check_status(('booked',), _('Convert to Group'))
        if self.teaching_is_group or self.teaching_invoice_ids:
            raise UserError(_('Only a confirmed, uninvoiced 1-to-1 session can be converted.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Convert to Group'),
            'res_model': 'teaching.convert.group',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_event_id': self.id},
        }

    def _teaching_do_convert_group(self, students):
        self.ensure_one()
        self._teaching_check_status(('booked',), _('Convert to Group'))
        if self.teaching_is_group or self.teaching_invoice_ids:
            raise UserError(_('Only a confirmed, uninvoiced 1-to-1 session can be converted.'))
        group_type = self.env.ref('teaching_course_management.appointment_type_group')
        old_type = self.appointment_type_id.name
        self.write({
            'appointment_type_id': group_type.id,
            'teaching_student_ids': [Command.link(s.id) for s in students],
        })
        self.message_post(body=_(
            'Converted from %(old)s to %(new)s. Each student is billed at the group rate. No credit note.',
            old=old_type, new=group_type.name))

    def action_teaching_promote(self):
        """Promote the first waiting student into the session (manual, never automatic)."""
        self.ensure_one()
        if not self.teaching_waitlist_ids:
            raise UserError(_('Nobody is on the waitlist.'))
        return self._teaching_promote(self.teaching_waitlist_ids[0])

    def _teaching_promote(self, partner):
        self.ensure_one()
        self._teaching_check_status(('request', 'booked'), _('Promote'))
        if partner not in self.teaching_waitlist_ids:
            raise UserError(_('%s is not on the waitlist.', partner.name))
        if self.teaching_seats_taken >= self.teaching_capacity:
            raise UserError(_('No seat is free yet.'))
        self.write({
            'teaching_student_ids': [Command.link(partner.id)],
            'teaching_waitlist_ids': [Command.unlink(partner.id)],
        })
        self.message_post(
            body=_('%s was promoted from the waitlist. A seat is confirmed.', partner.name),
            message_type='comment', subtype_xmlid='mail.mt_comment',
            partner_ids=(partner | partner.parent_id).ids)
        return True
