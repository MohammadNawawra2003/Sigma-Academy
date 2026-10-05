from datetime import datetime, time, timedelta

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

WEEKDAYS = [('mon', 'Mon'), ('tue', 'Tue'), ('wed', 'Wed'), ('thu', 'Thu'), ('fri', 'Fri'), ('sat', 'Sat'), ('sun', 'Sun')]


class TeachingCourse(models.Model):
    _name = 'teaching.course'
    _description = 'Course'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_date desc, id desc'

    def _default_appointment_type(self):
        return self.env.ref('teaching_course_management.appointment_type_course', raise_if_not_found=False)

    name = fields.Char(required=True, tracking=True)
    subject_id = fields.Many2one('teaching.subject', required=True)
    curriculum_id = fields.Many2one('teaching.curriculum', required=True)
    grade_id = fields.Many2one('teaching.grade', required=True)
    instructor_id = fields.Many2one('res.users', 'Instructor', required=True, default=lambda self: self.env.user)
    appointment_type_id = fields.Many2one(
        'appointment.type', 'Session Type', required=True, default=_default_appointment_type,
        domain=[('teaching_product_id', '!=', False)])
    currency_id = fields.Many2one(related='appointment_type_id.teaching_product_id.currency_id')
    price_per_session = fields.Float(
        'Price / Session / Student', related='appointment_type_id.teaching_product_id.lst_price', digits='Product Price')
    capacity = fields.Integer(default=7, tracking=True)
    student_ids = fields.Many2many(
        'res.partner', 'teaching_course_student_rel', 'course_id', 'partner_id', string='Enrolled Students',
        domain=[('is_student', '=', True)])
    seats_taken = fields.Integer(compute='_compute_seats')
    seats_available = fields.Integer(compute='_compute_seats')
    mon = fields.Boolean('Mon')
    tue = fields.Boolean('Tue')
    wed = fields.Boolean('Wed')
    thu = fields.Boolean('Thu')
    fri = fields.Boolean('Fri')
    sat = fields.Boolean('Sat')
    sun = fields.Boolean('Sun')
    start_hour = fields.Float('At', default=18.0)
    duration = fields.Float(default=1.0)
    start_date = fields.Date('From', required=True, default=fields.Date.context_today)
    end_type = fields.Selection([('count', 'Number of sessions'), ('end_date', 'End date')], 'Until', default='count', required=True)
    count = fields.Integer('Number of Sessions', default=10)
    until = fields.Date('End Date')
    schedule_summary = fields.Char('Schedule', compute='_compute_schedule_summary')
    session_ids = fields.One2many('calendar.event', 'teaching_course_id', 'Sessions')
    session_count = fields.Integer(compute='_compute_session_count')
    description = fields.Html()
    state = fields.Selection([
        ('draft', 'Draft'), ('confirmed', 'Confirmed'), ('running', 'Running'),
        ('done', 'Done'), ('cancelled', 'Cancelled')], default='draft', required=True, tracking=True)

    _capacity_positive = models.Constraint('CHECK(capacity >= 1)', 'The capacity must be at least 1.')
    _duration_positive = models.Constraint('CHECK(duration > 0)', 'The duration must be positive.')

    @api.depends('student_ids', 'capacity')
    def _compute_seats(self):
        for course in self:
            course.seats_taken = len(course.student_ids)
            course.seats_available = max(course.capacity - course.seats_taken, 0)

    @api.depends('session_ids')
    def _compute_session_count(self):
        for course in self:
            course.session_count = len(course.session_ids)

    @api.depends('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun', 'start_hour', 'start_date', 'end_type', 'count', 'until')
    def _compute_schedule_summary(self):
        for course in self:
            days = [label for fname, label in WEEKDAYS if course[fname]]
            if not days or not course.start_date:
                course.schedule_summary = False
                continue
            hours, minutes = divmod(round(course.start_hour * 60), 60)
            end = _('%s sessions', course.count) if course.end_type == 'count' else _(
                'until %s', course.until and course.until.strftime('%d %b %Y'))
            course.schedule_summary = _('%(days)s at %(time)s, from %(start)s, %(end)s',
                                        days=', '.join(days), time='%02d:%02d' % (hours, minutes),
                                        start=course.start_date.strftime('%d %b %Y'), end=end)

    @api.constrains('student_ids', 'capacity')
    def _check_capacity(self):
        for course in self:
            if len(course.student_ids) > course.capacity:
                raise ValidationError(_('The course is full (%s seats).', course.capacity))

    @api.constrains('until', 'start_date', 'end_type', 'count')
    def _check_dates(self):
        for course in self:
            if course.end_type == 'end_date' and course.until and course.until < course.start_date:
                raise ValidationError(_('The end date must be on or after the start date.'))
            if course.end_type == 'count' and course.count < 1:
                raise ValidationError(_('The number of sessions must be at least 1.'))

    # ------------------------------------------------------------------ buttons

    def action_confirm(self):
        for course in self:
            if course.state != 'draft':
                raise UserError(_('Only a draft course can be confirmed.'))
        self.state = 'confirmed'

    def _first_start_utc(self):
        tz = pytz.timezone(self.appointment_type_id.appointment_tz or self.instructor_id.tz or 'UTC')
        hours, minutes = divmod(round(self.start_hour * 60), 60)
        local = tz.localize(datetime.combine(self.start_date, time(hours, minutes)))
        return local.astimezone(pytz.utc).replace(tzinfo=None), tz.zone

    def action_generate_sessions(self):
        """One native recurring calendar.event: Odoo creates every date as its own event,
        which then follows the normal session workflow (attendance, billing, cancel)."""
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM teaching_course WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['session_ids'])
        if self.state != 'confirmed' or self.session_ids:
            raise UserError(_('Sessions are generated once, from a confirmed course without sessions.'))
        if not any(self[fname] for fname, _label in WEEKDAYS):
            raise UserError(_('Pick at least one weekday.'))
        if self.end_type == 'end_date' and not self.until:
            raise UserError(_('Set the end date.'))
        start, tz_name = self._first_start_utc()
        vals = {
            'name': self.name,
            'start': start,
            'stop': start + timedelta(hours=self.duration),
            'duration': self.duration,
            'user_id': self.instructor_id.id,
            'appointment_type_id': self.appointment_type_id.id,
            'appointment_status': 'booked',
            'teaching_course_id': self.id,
            'subject_id': self.subject_id.id,
            'teaching_student_ids': [Command.set(self.student_ids.ids)],
            'partner_ids': [Command.set(self.instructor_id.partner_id.ids)],
            'recurrency': True,
            'rrule_type': 'weekly',
            'interval': 1,
            'event_tz': tz_name,
            'end_type': self.end_type,
            'count': self.count,
            'until': self.until,
            **{fname: self[fname] for fname, _label in WEEKDAYS},
        }
        base = self.env['calendar.event'].create(vals)
        events = base.recurrence_id.calendar_event_ids or base
        # Recurrence copies stored values; make sure every occurrence is linked and staffed.
        events.filtered(lambda e: e.teaching_course_id != self).write({'teaching_course_id': self.id})
        missing = events.filtered(lambda e: e.teaching_student_ids != self.student_ids)
        if missing:
            missing.write({'teaching_student_ids': [Command.set(self.student_ids.ids)]})
        self.state = 'running'
        return self.action_view_sessions()

    def action_enrol(self):
        self.ensure_one()
        if self.seats_available <= 0:
            raise UserError(_('The course is full.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Enrol Student'),
            'res_model': 'teaching.course.enrol',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_course_id': self.id},
        }

    def _enrol(self, partner):
        """Add a student to the course and to every future, unbilled session."""
        self.ensure_one()
        if partner in self.student_ids:
            raise UserError(_('%s is already enrolled.', partner.name))
        if self.seats_available <= 0:
            raise UserError(_('The course is full.'))
        self.student_ids = [Command.link(partner.id)]
        future = self.session_ids.filtered(
            lambda e: e.start > fields.Datetime.now() and e.appointment_status in ('request', 'booked')
            and not e.teaching_invoice_ids)
        future.write({'teaching_student_ids': [Command.link(partner.id)]})
        self.message_post(body=_('%(student)s enrolled; added to %(n)s future sessions.', student=partner.name, n=len(future)))

    def action_publish_announcement(self):
        self.ensure_one()
        blog = self.env.ref('teaching_course_management.blog_announcements')
        post = self.env['blog.post'].create({
            'name': _('New course: %s', self.name),
            'subtitle': self.schedule_summary,
            'blog_id': blog.id,
            'teaching_course_id': self.id,
            'content': self.description,
            'is_published': True,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'blog.post',
            'res_id': post.id,
            'view_mode': 'form',
        }

    def action_done(self):
        self.state = 'done'

    def action_cancel(self):
        """Cancel the course and its future sessions that are not billed."""
        for course in self:
            future = course.session_ids.filtered(
                lambda e: e.start > fields.Datetime.now() and e.appointment_status in ('request', 'booked')
                and not e.teaching_invoice_ids)
            for event in future:
                event.action_set_appointment_cancelled()
        self.state = 'cancelled'

    def action_view_sessions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('teaching_course_management.action_teaching_sessions')
        action['domain'] = [('teaching_course_id', '=', self.id)]
        action['context'] = {'default_teaching_course_id': self.id, 'active_test': False}
        return action

    def action_view_students(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('teaching_course_management.action_teaching_students')
        action['domain'] = [('id', 'in', self.student_ids.ids)]
        return action
