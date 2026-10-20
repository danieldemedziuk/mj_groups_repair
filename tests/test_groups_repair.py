# -*- coding: utf-8 -*-
from odoo import Command
from odoo.tests import TransactionCase, tagged

from ..models.mj_groups_repair import TEMPLATE_COPY_SIGNATURE


@tagged('post_install', '-at_install')
class TestGroupsRepair(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.template = cls.env.ref('base.default_user')
        cls.system = cls.env.ref('base.group_system')
        cls.sanitize = cls.env.ref('base.group_sanitize_override')
        cls.designer = cls.env.ref('website.group_website_designer')
        cls.editor = cls.env.ref('website.group_website_restricted_editor')
        cls.account_manager = cls.env.ref('account.group_account_manager')
        cls.project_user = cls.env.ref('project.group_project_user')
        cls.employee = cls.env.ref('base.group_user')
        cls.signature = cls.env['res.groups']
        for xmlid in TEMPLATE_COPY_SIGNATURE:
            cls.signature |= cls.env.ref(xmlid)

    def _user(self, login, groups):
        # No password: the password policy in this database rejects the short
        # ones test helpers generate.
        return self.Users.create({
            'name': login,
            'login': login,
            'groups_id': [Command.set(groups.ids)],
        })

    def _link_without_propagation(self, user, group):
        """Put a group on the template the way the old data has it.

        Writing it through the ORM would push it on to every employee, which
        is exactly the mechanism under repair.
        """
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (group.id, user.id))
        self.env.invalidate_all()

    def _run(self, write=True):
        wizard = self.env['mj.groups.repair'].create({})
        if write:
            wizard.action_start()
        else:
            wizard.action_preview()
        return wizard.output

    def test_template_loses_admin_groups_without_touching_employees(self):
        self._link_without_propagation(self.template, self.account_manager)
        bystander = self._user('bystander@test', self.project_user)
        before = bystander.groups_id

        self._run()

        self.assertNotIn(self.account_manager, self.template.groups_id)
        self.assertNotIn(self.sanitize, self.template.groups_id)
        self.assertIn(self.employee, self.template.groups_id)
        self.assertEqual(bystander.groups_id, before)

    def test_template_copy_loses_administrator_groups(self):
        copy = self._user('copy@test', self.signature | self.project_user)

        output = self._run()

        self.assertIn('copy@test', output)
        for group in self.signature:
            self.assertNotIn(group, copy.groups_id, group.full_name)
        self.assertIn(self.project_user, copy.groups_id)
        self.assertIn(self.employee, copy.groups_id)

    def test_template_copy_keeps_baseline_held_only_through_admin_groups(self):
        # Timesheets User comes only from Timesheets Administrator here.
        timesheet_manager = self.env.ref('hr_timesheet.group_timesheet_manager')
        timesheet_user = self.env.ref('hr_timesheet.group_hr_timesheet_user')
        copy = self._user('copy@test', self.signature | timesheet_manager)
        self.assertIn(timesheet_user, copy.groups_id)

        self._run()

        self.assertNotIn(timesheet_manager, copy.groups_id)
        self.assertIn(timesheet_user, copy.groups_id)

    def test_partial_signature_is_not_a_copy(self):
        accountant = self._user(
            'accountant@test', self.account_manager | self.project_user)

        self._run()

        self.assertIn(self.account_manager, accountant.groups_id)

    def test_settings_administrators_are_left_alone(self):
        admin = self._user('admin2@test', self.system | self.signature)

        self._run()

        self.assertTrue(self.signature <= admin.groups_id)
        self.assertIn(self.sanitize, admin.groups_id)

    def test_sanitize_removed_and_designer_downgraded(self):
        plain = self._user('plain@test', self.project_user | self.sanitize)
        web = self._user('web@test', self.project_user | self.designer)
        self.assertIn(self.sanitize, web.groups_id)

        self._run()

        self.assertNotIn(self.sanitize, plain.groups_id)
        self.assertNotIn(self.sanitize, web.groups_id)
        self.assertNotIn(self.designer, web.groups_id)
        self.assertIn(self.editor, web.groups_id)
        self.assertIn(self.project_user, web.groups_id)

    def test_sanitize_removed_from_roles_and_their_users(self):
        role = self.env['res.users.role'].create({
            'name': 'Test role',
            'implied_ids': [Command.set((self.project_user | self.sanitize).ids)],
        })
        member = self._user('member@test', self.employee)
        member.write({'role_line_ids': [Command.create({'role_id': role.id})]})
        self.assertIn(self.sanitize, member.groups_id)

        self._run()

        self.assertNotIn(self.sanitize, role.implied_ids)
        self.assertNotIn(self.sanitize, member.groups_id)
        self.assertIn(self.project_user, member.groups_id)

    def _role(self, name):
        Role = self.env['res.users.role'].with_context(active_test=False)
        return Role.search([('name', '=', name)], limit=1) \
            or Role.create({'name': name})

    def test_role_name_typo_corrected(self):
        self.env['res.users.role'].with_context(active_test=False).search(
            [('name', '=', 'Inżynier projektu')]).unlink()
        role = self._role('Inżynierowi projektu')

        self._run()

        self.assertEqual(role.with_context(lang='en_US').name,
                         'Inżynier projektu')

    def test_role_rename_onto_existing_name_is_skipped(self):
        typo = self._role('Inżynierowi projektu')
        self._role('Inżynier projektu')

        output = self._run()

        self.assertIn('not renamed', output)
        self.assertEqual(typo.with_context(lang='en_US').name,
                         'Inżynierowi projektu')

    def test_preview_keeps_nothing(self):
        plain = self._user('plain@test', self.project_user | self.sanitize)
        copy = self._user('copy@test', self.signature | self.project_user)

        output = self._run(write=False)

        self.assertIn('NOTHING WAS KEPT', output)
        self.assertIn(self.sanitize, plain.groups_id)
        self.assertTrue(self.signature <= copy.groups_id)

    def test_second_run_changes_nothing(self):
        self._user('copy@test', self.signature | self.project_user)
        self._run()
        snapshot = {
            u.id: u.groups_id for u in self.env['res.users'].with_context(
                active_test=False).search([])
        }

        output = self._run()

        self.assertIn('removed from 0 account(s)', output)
        for user in self.env['res.users'].with_context(
                active_test=False).search([]):
            self.assertEqual(user.groups_id, snapshot[user.id], user.login)
