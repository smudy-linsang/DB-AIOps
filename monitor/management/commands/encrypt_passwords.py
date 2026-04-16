"""
管理命令：encrypt_passwords

将数据库中所有 DatabaseConfig 的明文密码一次性加密为 AES-256-GCM 密文。
已加密的记录自动跳过（幂等操作，可重复执行）。

用法：
    python manage.py encrypt_passwords
    python manage.py encrypt_passwords --dry-run   # 只预览，不写入
"""

from django.core.management.base import BaseCommand
from monitor.models import DatabaseConfig
from monitor.crypto import encrypt_password, is_encrypted


class Command(BaseCommand):
    help = '将所有 DatabaseConfig 明文密码加密为 AES-256-GCM 密文（幂等）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='只打印预览，不实际写入数据库'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        configs = DatabaseConfig.objects.all()
        total = configs.count()
        encrypted_count = 0
        skipped_count = 0

        self.stdout.write(f"共发现 {total} 条数据库配置记录。")
        if dry_run:
            self.stdout.write(self.style.WARNING('【DRY-RUN 模式，不会实际修改数据库】'))

        for cfg in configs:
            if is_encrypted(cfg.password):
                self.stdout.write(f"  跳过（已加密）：{cfg.name}")
                skipped_count += 1
                continue

            if not dry_run:
                cfg.password = encrypt_password(cfg.password)
                cfg.save(update_fields=['password'])
                self.stdout.write(self.style.SUCCESS(f"  已加密：{cfg.name}"))
            else:
                self.stdout.write(f"  将加密：{cfg.name}")
            encrypted_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n完成。加密：{encrypted_count} 条，跳过（已加密）：{skipped_count} 条。"
            )
        )
