from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'settings の USER_GROUPS / GROUP_ROLE_PERMISSIONS を Group に同期します。'

    def handle(self, *args, **options):
        configured_groups = list(getattr(settings, 'USER_GROUPS', []))
        role_permissions = dict(getattr(settings, 'GROUP_ROLE_PERMISSIONS', {}))
        all_group_names = list(dict.fromkeys([*configured_groups, *role_permissions.keys()]))

        for group_name in all_group_names:
            group, _ = Group.objects.get_or_create(name=group_name)
            group.permissions.clear()

            for permission_name in role_permissions.get(group_name, []):
                try:
                    app_label, codename = permission_name.split('.', 1)
                    permission = Permission.objects.get(
                        content_type__app_label=app_label,
                        codename=codename,
                    )
                    group.permissions.add(permission)
                except Permission.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f'権限が見つかりません: {permission_name}'))
                except ValueError:
                    self.stdout.write(self.style.WARNING(f'権限形式が不正です: {permission_name}'))

        self.stdout.write(self.style.SUCCESS('グループと権限の同期が完了しました。'))
