from odoo import api, models


class AccountFollowupLine(models.Model):
    _inherit = 'account_followup.followup.line'

    @api.model
    def _teaching_ensure_monthly_level(self):
        """R13: one automatic monthly statement (30 days) by email. It never creates invoices.
        Reuses the company's 30-day level when it exists (delay is unique per company).
        WhatsApp is switched on later, once a Meta-approved template exists."""
        company = self.env.company
        level = self.search([('company_id', '=', company.id), ('delay', '=', 30)], limit=1)
        vals = {'name': 'Monthly statement', 'auto_execute': True, 'send_email': True}
        if level:
            level.write(vals)
        else:
            self.create({**vals, 'delay': 30, 'company_id': company.id})
