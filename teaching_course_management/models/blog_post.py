from odoo import fields, models


class BlogPost(models.Model):
    _inherit = 'blog.post'

    teaching_course_id = fields.Many2one('teaching.course', 'Course', ondelete='set null')
