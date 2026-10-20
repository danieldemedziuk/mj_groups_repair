# -*- coding: utf-8 -*-
"""Clean-up of group memberships, driven from the interface.

Only memberships change here: who is in a group, what the new-user template
carries, what a role implies. What a group *is* - its implied groups, its
category, whether it exists at all - is declared in the XML of the module
that owns it, and the next update would undo any change made from here. Those
fixes went into the modules themselves.

Transactions
------------
Everything runs inside one savepoint. Preview rolls it back and keeps only
the report; Start releases it and the request commits. A refusal along the
way unwinds the same way and is written into the report instead of a dialog.
"""

from collections import defaultdict

from odoo import Command, _, fields, models
from odoo.exceptions import UserError

# What every employee gets. Used as the only content allowed on the new-user
# template - never to grant anything to existing users.
EMPLOYEE_BASELINE = (
    'base.group_user',
    'base.group_allow_export',
    'base.group_partner_manager',
    'mail.group_mail_notification_type_inbox',
    'business_delegations.group_business_delegations_user',
    'hr_delegations.group_hr_delegations_user',
    'car_reservation.group_car_reservation_user',
    'daily_task.group_daily_task_user',
    'help_request.group_help_request_user',
    'hr_document_flow.group_hr_document_flow_user',
    'hr_employee_lifecycle.group_hr_employee_lifecycle_user',
    'hr_extension.group_hr_employee_user',
    'hr_overtime.group_hr_overtime_user',
    'hr_timesheet.group_hr_timesheet_user',
    'project.group_project_user',
    'project_charter.group_project_charter_user',
    'project_purchase_lines.group_project_purchase_lines_user',
    'project_timesheet_control.group_requests_user',
)

# Access to every record of an application (Administrator, or Officer where
# the core Officer already means "all requests of all employees").
ELEVATED_GROUPS = (
    'account.group_account_manager',
    'fleet.fleet_group_manager',
    'hr.group_hr_manager',
    'hr_contract.group_hr_contract_manager',
    'hr_holidays.group_hr_holidays_manager',
    'hr_holidays.group_hr_holidays_user',
    'hr_attendance.group_hr_attendance_manager',
    'hr_recruitment.group_hr_recruitment_manager',
    'hr_timesheet.group_timesheet_manager',
    'project.group_project_manager',
    'purchase.group_purchase_manager',
    'sales_team.group_sale_manager',
    'stock.group_stock_manager',
    'survey.group_survey_manager',
    'website.group_website_designer',
)

# A copy of the old template carries all of these at once. Nobody who holds
# them legitimately holds all six without also being a Settings
# administrator - Purchase and Inventory are not used, and Website Designer is
# a marketing right - so the combination identifies the copies and nobody else.
TEMPLATE_COPY_SIGNATURE = (
    'account.group_account_manager',
    'hr.group_hr_manager',
    'purchase.group_purchase_manager',
    'stock.group_stock_manager',
    'sales_team.group_sale_manager',
    'website.group_website_designer',
)

# The same right - the whole reporting line below a person - declared once per
# module. Handing out four of six is the usual mistake.
SENIOR_SUPERVISOR_GROUPS = (
    'business_delegations.group_business_delegations_senior_supervisor',
    'daily_task.group_daily_task_senior_officer',
    'hr_delegations.group_hr_delegations_senior_officer',
    'hr_extension.group_hr_timesheet_senior_manager',
    'hr_leave_extension.group_hr_leave_senior_manager',
    'hr_overtime.group_hr_overtime_senior_manager',
)

ROLE_NAME_FIXES = {
    'Inżynierowi projektu': 'Inżynier projektu',
}


class _DryRun(Exception):
    """Raised to unwind the savepoint once the report is written."""


class MjGroupsRepair(models.TransientModel):
    _name = 'mj.groups.repair'
    _description = 'Groups Repair (one-off)'

    output = fields.Text(string='Report', readonly=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _groups(self, xmlids):
        """The groups behind `xmlids` that exist in this database."""
        groups = self.env['res.groups']
        for xmlid in xmlids:
            groups |= self.env.ref(xmlid, raise_if_not_found=False) or groups
        return groups

    def _label(self, groups):
        xmlids = groups.get_external_id()
        return ', '.join(sorted(xmlids.get(g.id) or g.full_name for g in groups))

    def _logins(self, users):
        return ', '.join(sorted(users.mapped('login'))) or '-'

    def _explicit(self, user):
        """The groups a user holds that no other held group implies.

        The relation table stores the closure, so this is the closest thing
        to "what was ticked" the database still knows.
        """
        return user.groups_id - user.groups_id.trans_implied_ids

    def _set_explicit(self, user, explicit):
        target = explicit | explicit.trans_implied_ids
        if target != user.groups_id:
            user.write({'groups_id': [Command.set(target.ids)]})

    def _people(self):
        """Internal accounts, archived ones included, without the template.

        Archived accounts are included because reactivating one restores
        whatever it held.
        """
        template = self.env.ref('base.default_user', raise_if_not_found=False)
        users = self.env['res.users'].with_context(active_test=False).search(
            [('share', '=', False)])
        return users - template if template else users

    def _settings_admins(self):
        return self.env.ref('base.group_system').with_context(
            active_test=False).users

    # ------------------------------------------------------------------
    # steps
    # ------------------------------------------------------------------
    def _step_template(self, log):
        log("1. NEW-USER TEMPLATE (base.default_user)")
        template = self.env.ref('base.default_user', raise_if_not_found=False)
        if not template:
            log("   base.default_user does not exist - nothing to do")
            return
        baseline = self._groups(EMPLOYEE_BASELINE)
        allowed = baseline | baseline.trans_implied_ids
        current = template.groups_id
        keep = current & allowed
        drop = current - keep
        if not drop:
            log("   clean: %s groups, all within the employee baseline"
                % len(current))
            return
        log("   removing %s group(s):" % len(drop))
        for group in drop.sorted('full_name'):
            log("     - %s" % self._label(group))
        # Command.set with a subset of what is there. The core pushes groups
        # *added* to the template on to every employee; a subset adds
        # nothing, so no existing user is touched.
        template.write({'groups_id': [Command.set(keep.ids)]})
        log("   template: %s -> %s groups"
            % (len(current), len(template.groups_id)))
        missing = baseline - template.groups_id
        if missing:
            log("   not added (would be pushed to every employee - decide "
                "first): %s" % self._label(missing))

    def _step_template_copies(self, log):
        log("2. ACCOUNTS COPIED FROM THE OLD TEMPLATE")
        signature = self._groups(TEMPLATE_COPY_SIGNATURE)
        if len(signature) < len(TEMPLATE_COPY_SIGNATURE):
            log("   not every signature group is installed - skipped")
            return
        admins = self._settings_admins()
        copies = self._people().filtered(
            lambda u: signature <= u.groups_id and u not in admins)
        if not copies:
            log("   none")
            return
        elevated = self._groups(ELEVATED_GROUPS)
        baseline = self._groups(EMPLOYEE_BASELINE)
        for user in copies.sorted('login'):
            if user.role_line_ids:
                log("   %s: has roles - the role sync owns its groups, "
                    "change the role instead" % user.login)
                continue
            before = user.groups_id
            explicit = self._explicit(user)
            # Baseline groups held only through an administrator group would
            # otherwise go with it; the employee keeps what every employee has.
            self._set_explicit(user, (explicit - elevated)
                               | (before & baseline))
            log("   %s: removed %s" % (user.login,
                                       self._label(before - user.groups_id)))
            gap = baseline - user.groups_id
            if gap:
                log("     still missing the employee baseline: %s"
                    % self._label(gap))

    def _step_sanitize(self, log):
        log("3. BYPASS HTML FIELD SANITIZE - Settings administrators only")
        sanitize = self.env.ref('base.group_sanitize_override')
        admins = self._settings_admins()

        # Roles first: the role sync rewrites a role holder's groups on every
        # login and every cron run, so a user fixed before the role would get
        # it straight back.
        roles = self.env['res.users.role'].with_context(
            active_test=False).search([])
        for role in roles.filtered(lambda r: sanitize in r.implied_ids):
            role.write({'implied_ids': [Command.unlink(sanitize.id)]})
            log("   role %s: removed" % role.name)
        for role in roles.filtered(
                lambda r: sanitize in r.implied_ids.trans_implied_ids):
            log("   role %s: still gets it through another group - fix "
                "the role by hand" % role.name)

        # Website Designer implies it, and writing a user's groups re-adds
        # whatever their groups imply. Restricted Editor keeps page editing.
        designer = self.env.ref('website.group_website_designer',
                                raise_if_not_found=False)
        editor = self.env.ref('website.group_website_restricted_editor',
                              raise_if_not_found=False)
        if designer and editor:
            designers = designer.with_context(active_test=False).users
            for user in (designers & self._people() - admins).sorted('login'):
                if user.role_line_ids:
                    log("   %s: Website Designer comes from a role - fix "
                        "the role" % user.login)
                    continue
                explicit = self._explicit(user)
                self._set_explicit(user, (explicit - designer) | editor)
                log("   %s: Website Designer -> Restricted Editor"
                    % user.login)

        holders = sanitize.with_context(active_test=False).users \
            & self._people() - admins
        stuck = holders.filtered(
            lambda u: sanitize in (u.groups_id - sanitize).trans_implied_ids)
        removable = holders - stuck
        if removable:
            removable.write({'groups_id': [Command.unlink(sanitize.id)]})
        log("   removed from %s account(s), archived ones included"
            % len(removable))
        if stuck:
            log("   kept, another group implies it: %s" % self._logins(stuck))
        log("   Settings administrators keep it: %s" % self._logins(
            admins.filtered(lambda u: not u.share)))

    def _step_role_names(self, log):
        log("4. ROLE NAMES")
        langs = {code for code, _name in self.env['res.lang'].get_installed()}
        langs.add('en_US')
        fixed = False
        roles = self.env['res.users.role'].with_context(
            active_test=False).search([])
        for role in roles:
            translations, _context = role.group_id.get_field_translations(
                'name', langs=langs)
            updates = {
                row['lang']: ROLE_NAME_FIXES[row['value']]
                for row in translations if row['value'] in ROLE_NAME_FIXES
            }
            if updates:
                old = role.name
                # Group names are unique per category: renaming onto a role
                # that already carries the right name would abort the run.
                taken = self.env['res.groups'].with_context(
                    active_test=False).search_count([
                        ('id', '!=', role.group_id.id),
                        ('category_id', '=', role.group_id.category_id.id),
                        ('name', 'in', list(set(updates.values()))),
                    ])
                if taken:
                    log("   %s: not renamed, %s already exists - merge the "
                        "two roles by hand" % (old, next(iter(updates.values()))))
                    continue
                role.group_id.update_field_translations('name', updates)
                log("   %s -> %s (%s)" % (old, next(iter(updates.values())),
                                         ', '.join(sorted(updates))))
                fixed = True
        if not fixed:
            log("   nothing to correct")

    def _step_audit(self, log):
        """What is left for a person to decide. Changes nothing."""
        log("5. LEFT FOR A DECISION (nothing below was changed)")
        people = self._people().filtered('active')
        admins = self._settings_admins()

        log("")
        log("   Settings administrators: %s" % self._logins(
            admins.filtered(lambda u: u.active and not u.share)))

        log("")
        log("   Application-wide rights, outside Settings:")
        for group in self._groups(ELEVATED_GROUPS).sorted('full_name'):
            members = group.users & people - admins
            if members:
                log("     %-55s %3s  %s" % (self._label(group), len(members),
                                           self._logins(members)))

        sanitize = self.env.ref('base.group_sanitize_override')
        left = sanitize.users & people - admins
        log("")
        log("   Bypass HTML Field Sanitize outside Settings: %s"
            % (self._logins(left) if left else 'nobody'))

        baseline = self._groups(EMPLOYEE_BASELINE)
        allowed = baseline | baseline.trans_implied_ids
        never = people.filtered(lambda u: not u.login_date)
        beyond = never.filtered(lambda u: u.groups_id - allowed)
        log("")
        log("   Never logged in, with more than the baseline: %s"
            % self._logins(beyond))

        by_name = defaultdict(lambda: self.env['res.users'])
        for user in people:
            by_name[user.name] |= user
        doubles = [users for users in by_name.values() if len(users) > 1]
        log("")
        log("   One person, several active accounts:")
        for users in sorted(doubles, key=lambda u: u[0].name):
            log("     %s: %s" % (users[0].name, ', '.join(
                '%s (%s groups)' % (u.login, len(u.groups_id))
                for u in users.sorted('login'))))
        if not doubles:
            log("     none")

        senior = self._groups(SENIOR_SUPERVISOR_GROUPS)
        # Daily task Manager implies its Senior officer, so that one alone
        # says nothing about anybody being meant as a senior supervisor.
        daily = self.env.ref('daily_task.group_daily_task_senior_officer',
                             raise_if_not_found=False)
        partial = people.filtered(
            lambda u: u.groups_id & (senior - daily)
            and not senior <= u.groups_id)
        log("")
        log("   Senior supervisor - %s groups, one meaning. Holding only "
            "some of them:" % len(senior))
        for user in partial.sorted('login'):
            log("     %s: missing %s" % (user.login,
                                         self._label(senior - user.groups_id)))
        if not partial:
            log("     none")

        roles = self.env['res.users.role'].search([])
        same = defaultdict(lambda: self.env['res.users.role'])
        for role in roles:
            same[frozenset(role.implied_ids.ids)] |= role
        log("")
        log("   Roles granting exactly the same groups:")
        twins = [r for r in same.values() if len(r) > 1]
        for group_of_roles in twins:
            log("     %s" % ', '.join(group_of_roles.mapped('name')))
        if not twins:
            log("     none")

        without_role = people.filtered(lambda u: not u.role_line_ids)
        log("")
        log("   Employee baseline gaps (%s accounts without a role):"
            % len(without_role))
        for group in baseline.sorted('full_name'):
            lacking = without_role - group.users
            if lacking:
                log("     %-55s %3s" % (self._label(group), len(lacking)))

    # ------------------------------------------------------------------
    # the buttons
    # ------------------------------------------------------------------
    def action_start(self):
        """Run the repair and keep the result."""
        return self._execute(write=True)

    def action_preview(self):
        """The same run, with nothing kept."""
        return self._execute(write=False)

    def _execute(self, write):
        self.ensure_one()
        entries = []
        log = entries.append
        log("mode: %s" % ("REPAIRING" if write else "PREVIEW - nothing kept"))
        log("=" * 70)
        try:
            with self.env.cr.savepoint():
                for step in (self._step_template,
                             self._step_template_copies,
                             self._step_sanitize,
                             self._step_role_names,
                             self._step_audit):
                    step(log)
                    log("")
                if not write:
                    raise _DryRun()
        except _DryRun:
            log("NOTHING WAS KEPT.")
        except UserError as error:
            # Reported rather than raised: a dialog would replace the log
            # with a one-line message. The savepoint has already unwound.
            log("")
            log("STOPPED: %s" % error)
            log("NOTHING WAS KEPT.")

        # Group writes fill the registry caches (has_group, rules, menus); a
        # rolled-back preview must not leave them describing what was undone.
        self.env.registry.clear_cache()
        self.env.invalidate_all()
        record = self.browse(self.id)
        record.output = '\n'.join(entries)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': record.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('MJ Groups Repair'),
        }
