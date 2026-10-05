from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_student = fields.Boolean('Is Student', index=True, tracking=True)
    is_guardian = fields.Boolean('Is Guardian', compute='_compute_is_guardian', store=True)
    school_id = fields.Many2one('teaching.school', 'School')
    school_is_other = fields.Boolean(related='school_id.is_other')
    school_other = fields.Char('School (Other)')
    grade_id = fields.Many2one('teaching.grade', 'Grade')
    curriculum_id = fields.Many2one('teaching.curriculum', 'Curriculum')
    subject_ids = fields.Many2many('teaching.subject', string='Subjects')
    teaching_invoice_to = fields.Selection(
        [('guardian', 'Guardian'), ('student', 'Student')], 'Invoice To', default='guardian',
        help='Who receives the invoices for this student. Falls back to the student when no guardian is set.')
    teaching_session_count = fields.Integer('Sessions', compute='_compute_teaching_counts')
    teaching_course_count = fields.Integer('Courses', compute='_compute_teaching_counts')

    @api.depends('child_ids.is_student')
    def _compute_is_guardian(self):
        for partner in self:
            partner.is_guardian = any(partner.child_ids.mapped('is_student'))

    def _compute_teaching_counts(self):
        Event = self.env['calendar.event']
        Course = self.env['teaching.course']
        for partner in self:
            students = partner if partner.is_student else partner.child_ids.filtered('is_student')
            partner.teaching_session_count = Event.search_count(
                [('is_teaching', '=', True), ('teaching_student_ids', 'in', students.ids)]) if students else 0
            partner.teaching_course_count = Course.search_count(
                [('student_ids', 'in', students.ids)]) if students else 0

    def _teaching_family_students(self):
        """Students this partner may act for: itself if it is a student, else its student children."""
        self.ensure_one()
        return self if self.is_student else self.child_ids.filtered('is_student')

    def action_teaching_sessions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('teaching_course_management.action_teaching_sessions')
        action['domain'] = [('is_teaching', '=', True), ('teaching_student_ids', 'in', self._teaching_family_students().ids)]
        action['context'] = {'search_default_upcoming': 0}
        return action

    def action_teaching_courses(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('teaching_course_management.action_teaching_course')
        action['domain'] = [('student_ids', 'in', self._teaching_family_students().ids)]
        return action

    def action_teaching_promote_waitlist(self):
        """Row button on a session's Waitlist tab."""
        self.ensure_one()
        event = self.env['calendar.event'].browse(self.env.context.get('teaching_event_id')).exists()
        if not event:
            return False
        return event._teaching_promote(self)
