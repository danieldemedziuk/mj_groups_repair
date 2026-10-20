{
    'name': 'MJ Groups Repair (temporary)',
    'version': '17.0.1.0.0',
    'author': 'DSquare Net',
    'company': 'MJ Group Sp. z o.o.',
    'license': 'OPL-1',
    'summary': 'One-off clean-up of user group memberships',
    'description': """
MJ Groups Repair
================
A disposable module. Install it, run the repair from
Settings > Technical > MJ Groups Repair, then uninstall it.

It changes who belongs to which group - never what a group is. Group
definitions live in the XML of the modules that own them and an update
would put any change made here back, so those fixes went into the modules
themselves.

What it does, in this order:

1. The new-user template (base.default_user) loses every group outside
   the employee baseline. New users are cloned from it, so administrator
   rights there were handed to every account created without hand-picked
   groups.
2. Accounts that are a copy of that template - all administrator groups
   at once, outside the Settings group - lose those administrator groups.
3. "Bypass HTML Field Sanitize" stays with Settings administrators only:
   taken off users and roles, and Website Designer (which implies it) is
   replaced by Restricted Editor.
4. Obvious typos in role names are corrected.
5. An audit of what is left for a person to decide.

The full diagnosis is in AUDYT_GRUP_2026-10-13.md.
    """,
    'category': 'Hidden/Tools',
    'depends': [
        # res.users.role: sanitize has to come off the roles too, otherwise
        # the role sync puts it back on every login and every cron run.
        'base_user_role',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/mj_groups_repair_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
