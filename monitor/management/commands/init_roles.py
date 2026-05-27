"""
初始化内置角色和权限

用法: python manage.py init_roles [--force]

--force: 强制重建所有内置角色的权限（不删除自定义角色）
"""

from django.core.management.base import BaseCommand
from monitor.models import Role, RolePermission
from monitor.auth import BUILTIN_ROLES_META, BUILTIN_ROLE_PERMISSIONS


class Command(BaseCommand):
    help = '初始化内置角色和权限（RBAC v2.0）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='强制重建所有内置角色的权限',
        )

    def handle(self, *args, **options):
        force = options['force']

        self.stdout.write(self.style.NOTICE('开始初始化内置角色...'))

        for role_code, meta in BUILTIN_ROLES_META.items():
            # 创建或获取角色
            role, created = Role.objects.get_or_create(
                code=role_code,
                defaults={
                    'name': meta['name'],
                    'description': meta['description'],
                    'is_builtin': True,
                }
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f'  创建角色: {meta["name"]} ({role_code})'))
            else:
                # 更新名称和描述
                role.name = meta['name']
                role.description = meta['description']
                role.is_builtin = True
                role.save()
                self.stdout.write(f'  角色已存在: {meta["name"]} ({role_code})')

            # 如果 --force 或角色刚创建，则更新权限
            if force or created:
                # 清除旧权限
                RolePermission.objects.filter(role=role).delete()

                # 创建新权限
                perm_codes = BUILTIN_ROLE_PERMISSIONS.get(role_code, [])
                for perm_code in perm_codes:
                    RolePermission.objects.get_or_create(
                        role=role,
                        permission_code=perm_code,
                )
                self.stdout.write(self.style.SUCCESS(
                    f'    设置 {len(perm_codes)} 个权限'
                ))
            else:
                perm_count = RolePermission.objects.filter(role=role).count()
                self.stdout.write(f'    已有 {perm_count} 个权限（使用 --force 强制更新）')

        # 确保默认 admin 用户有超级管理员角色
        self._ensure_admin_role()

        self.stdout.write(self.style.SUCCESS('\n内置角色初始化完成!'))

    def _ensure_admin_role(self):
        """确保默认 admin 用户拥有超级管理员角色"""
        from django.contrib.auth.models import User
        from monitor.models import UserProfile

        try:
            admin_user = User.objects.get(username='admin')
            profile, created = UserProfile.objects.get_or_create(
                user=admin_user,
                defaults={'role': None}
            )

            if profile.role is None or profile.role.code != 'super_admin':
                super_admin_role = Role.objects.get(code='super_admin')
                profile.role = super_admin_role
                profile.save()
                self.stdout.write(self.style.SUCCESS(
                    '  已将 admin 用户设置为超级管理员'
                ))
            else:
                self.stdout.write('  admin 用户已是超级管理员')

        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING('  admin 用户不存在，跳过'))
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR('  super_admin 角色不存在，请先运行 init_roles'))
