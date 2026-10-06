import base64
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import MO, relativedelta

from odoo import Command, api, models
from odoo.tools import file_open

TZ = pytz.timezone('Asia/Hebron')


def _utc(day, hour, minute=0):
    return TZ.localize(datetime.combine(day, time(hour, minute))).astimezone(pytz.utc).replace(tzinfo=None)


def _day(offset):
    """Today + offset, skipping Thursdays (the demo course runs Thursday 17:00)."""
    step = 1 if offset >= 0 else -1
    day = date.today() + timedelta(days=offset)
    while day.weekday() == 3:
        day += timedelta(days=step)
    return day


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _teaching_demo_setup_company(self):
        company = self.env.ref('base.main_company')
        if not company.chart_template:
            self.env['account.chart.template'].try_loading('generic_coa', company, install_demo=False)
        # After the chart: loading a template resets the currency to the template's one.
        if not self.env['account.move.line'].search_count([('company_id', '=', company.id)], limit=1):
            company.write({'currency_id': self.env.ref('base.ILS').id, 'country_id': self.env.ref('base.ps').id})
        if company.name == 'My Company':  # untouched fresh database only
            with file_open('teaching_course_management/static/description/icon.png', 'rb') as f:
                company.write({'name': 'Sigma Academy', 'logo': base64.b64encode(f.read())})

    @api.model
    def _teaching_demo_ensure_cash_journal(self):
        """W1/R12: payments are cash or bank. The generic chart creates a Bank journal only."""
        company = self.env.ref('base.main_company')
        if company.chart_template and not self.env['account.journal'].search_count(
                [('type', '=', 'cash'), ('company_id', '=', company.id)], limit=1):
            self.env['account.journal'].create({'name': 'Cash', 'type': 'cash', 'code': 'CSH1', 'company_id': company.id})

    @api.model
    def _teaching_demo_load(self):
        ref = self.env.ref
        Event = self.env['calendar.event'].with_context(teaching_skip_invoice_send=True, mail_create_nosubscribe=True)
        sari = ref('teaching_course_management_demo.user_sari')
        one, group, review = (ref('teaching_course_management.appointment_type_%s' % x) for x in ('one_to_one', 'group', 'review'))
        physics, maths = ref('teaching_course_management_demo.subject_physics'), ref('teaching_course_management_demo.subject_maths')
        st = {x: ref('teaching_course_management_demo.student_%s' % x) for x in ('layan', 'karim', 'rami', 'omar', 'nour', 'lina')}

        # Published availability (Flexible schedule = dated slots), from next Monday on
        monday = date.today() + relativedelta(days=1, weekday=MO)
        slots = [(0, 17, 0, 1), (0, 18, 0, 1), (0, 19, 15, 1), (2, 17, 0, 2), (7, 9, 0, 2), (1, 16, 0, 1), (3, 20, 0, 1)]
        one.write({'slot_ids': [Command.create({
            'slot_type': 'unique',
            'start_datetime': _utc(monday + timedelta(days=d), h, m),
            'end_datetime': _utc(monday + timedelta(days=d), h, m) + timedelta(hours=dur),
        }) for d, h, m, dur in slots]})

        def session(students, atype, subject, day, hour, minute=0, duration=1.0, status='booked', **extra):
            start = _utc(day, hour, minute)
            return Event.create({
                'name': '%s — %s %s' % (', '.join(s.name for s in students), subject.name, atype.name),
                'user_id': sari.id,
                'partner_ids': [Command.link(sari.partner_id.id)],
                'appointment_type_id': atype.id,
                'subject_id': subject.id,
                'teaching_student_ids': [Command.set([s.id for s in students])],
                'start': start,
                'stop': start + timedelta(hours=duration),
                'duration': duration,
                'appointment_status': status,
                **extra,
            })

        # Rami, last month: 2:00 × 150 = 300, invoiced to Rami (Invoice To = Student), paid by bank
        paid = session([st['rami']], one, physics, _day(-30), 17, duration=2.0)
        paid.action_set_appointment_attended()
        paid.action_teaching_bill()
        bank = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.env.company.id)], limit=1)
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=paid.teaching_invoice_ids.ids,
        ).create({'journal_id': bank.id})._create_payments()

        # Layan: attended, actual 1:30 → 225 to the Odeh Family, unpaid
        attended = session([st['layan']], one, physics, _day(-2), 17,
                           teaching_notes='<p>Covered kinematics graphs. Homework: past paper Q3–Q6.</p>')
        attended.action_set_appointment_attended()
        attended.teaching_actual_duration = 1.5
        attended.action_teaching_bill()

        session([st['layan']], one, maths, _day(1), 18, 15)
        session([st['rami']], one, physics, _day(2), 16, status='request')
        session([st['layan'], st['karim'], st['rami']], group, maths, _day(3), 17)
        late = session([st['nour']], one, maths, _day(-1), 17)
        late.write({'appointment_status': 'late_cancelled', 'teaching_actual_duration': 1.0})
        no_show = session([st['karim']], review, maths, _day(-1), 19)
        no_show.action_set_appointment_no_show()
        full = session([st['layan'], st['karim'], st['rami'], st['omar'], st['lina']], group, maths, _day(4), 20)
        full.teaching_waitlist_ids = [Command.link(st['nour'].id)]

        # Courses
        Course = self.env['teaching.course']
        last_thursday = date.today() + relativedelta(weeks=-3, weekday=3)
        autumn = Course.create({
            'name': 'IAL Physics Intensive – Autumn 2026',
            'subject_id': physics.id,
            'curriculum_id': ref('teaching_course_management_demo.curriculum_ial').id,
            'grade_id': ref('teaching_course_management_demo.grade_12').id,
            'instructor_id': sari.id,
            'capacity': 7,
            'thu': True,
            'start_hour': 17.0,
            'start_date': last_thursday,
            'end_type': 'count',
            'count': 10,
            'student_ids': [Command.set([st['layan'].id, st['rami'].id])],
            'description': '<p>Ten live sessions covering the IAL Physics Unit 4 syllabus with past-paper practice.</p>',
        })
        autumn.action_confirm()
        autumn.action_generate_sessions()
        winter = Course.create({
            'name': 'IGCSE Maths Revision – Winter 2026',
            'subject_id': maths.id,
            'curriculum_id': ref('teaching_course_management_demo.curriculum_igcse').id,
            'grade_id': ref('teaching_course_management_demo.grade_10').id,
            'instructor_id': sari.id,
            'capacity': 7,
            'mon': True,
            'wed': True,
            'start_hour': 18.0,
            'start_date': monday + timedelta(weeks=1),
            'end_type': 'end_date',
            'until': monday + timedelta(weeks=8),
            'description': '<p>Full IGCSE Maths revision before the winter exams: algebra, geometry, statistics.</p>',
        })
        winter.action_confirm()
        winter.action_publish_announcement()
        self.env['blog.post'].create({
            'name': 'IAL Physics Intensive starts Thursday',
            'subtitle': 'Thursdays at 17:00, ten live sessions',
            'blog_id': ref('teaching_course_management.blog_announcements').id,
            'teaching_course_id': autumn.id,
            'content': '<p>The IAL Physics Intensive runs every Thursday at 17:00. Seats are limited to 7.</p>',
            'is_published': True,
        })
