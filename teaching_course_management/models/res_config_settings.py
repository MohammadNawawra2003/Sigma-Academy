from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    teaching_gap_minutes = fields.Integer(
        'Gap Between Sessions (min)', config_parameter='teaching_course_management.gap_minutes', default=15)
    teaching_cutoff_hour = fields.Float(
        'Same-day Cut-off', config_parameter='teaching_course_management.cutoff_hour', default=12.0)
    teaching_group_capacity = fields.Integer(
        'Default Group Capacity', config_parameter='teaching_course_management.group_capacity', default=5)
