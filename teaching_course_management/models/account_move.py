from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    teaching_student_id = fields.Many2one('res.partner', 'Student', index=True, copy=False)
    teaching_school_id = fields.Many2one(related='teaching_student_id.school_id', string='School', store=True)
    teaching_amount_received = fields.Monetary(
        'Received', compute='_compute_teaching_amount_received', store=True, currency_field='company_currency_id',
        help='Registered payments: total minus what is still due (company currency).')
    teaching_event_ids = fields.Many2many(
        'calendar.event', 'teaching_event_invoice_rel', 'move_id', 'event_id', string='Sessions', copy=False, readonly=True)

    @api.depends('amount_total_signed', 'amount_residual_signed')
    def _compute_teaching_amount_received(self):
        for move in self:
            move.teaching_amount_received = move.amount_total_signed - move.amount_residual_signed
