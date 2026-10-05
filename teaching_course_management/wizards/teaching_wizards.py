from odoo import fields, models


class TeachingSessionReject(models.TransientModel):
    _name = 'teaching.session.reject'
    _description = 'Reject a booking request'

    event_id = fields.Many2one('calendar.event', required=True, ondelete='cascade')
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.event_id._teaching_do_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class TeachingConvertGroup(models.TransientModel):
    _name = 'teaching.convert.group'
    _description = 'Convert a 1-to-1 session to a group session'

    event_id = fields.Many2one('calendar.event', required=True, ondelete='cascade')
    student_ids = fields.Many2many('res.partner', string='Students to Add', domain=[('is_student', '=', True)])

    def action_confirm(self):
        self.event_id._teaching_do_convert_group(self.student_ids)
        return {'type': 'ir.actions.act_window_close'}


class TeachingCourseEnrol(models.TransientModel):
    _name = 'teaching.course.enrol'
    _description = 'Enrol a student in a course'

    course_id = fields.Many2one('teaching.course', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', 'Student', required=True, domain=[('is_student', '=', True)])

    def action_confirm(self):
        self.course_id._enrol(self.partner_id)
        return {'type': 'ir.actions.act_window_close'}
