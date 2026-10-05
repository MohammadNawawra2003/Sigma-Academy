from odoo import fields, models


class TeachingSubject(models.Model):
    _name = 'teaching.subject'
    _description = 'Subject'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    instructor_ids = fields.Many2many('res.users', string='Instructors')

    _name_uniq = models.Constraint('UNIQUE(name)', 'This subject already exists.')


class TeachingCurriculum(models.Model):
    _name = 'teaching.curriculum'
    _description = 'Curriculum'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class TeachingGrade(models.Model):
    _name = 'teaching.grade'
    _description = 'Grade'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class TeachingSchool(models.Model):
    _name = 'teaching.school'
    _description = 'School'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    city = fields.Char()
    is_other = fields.Boolean('Is "Other"', help='Selecting this school asks for the school name in a free-text field.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
