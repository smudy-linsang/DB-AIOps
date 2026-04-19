"""将仍为明文的数据库连接密码批量加密为 AES-256-GCM（enc: 前缀）。"""

from django.core.management.base import BaseCommand

from monitor.crypto import is_encrypted
from monitor.models import DatabaseConfig


class Command(BaseCommand):
    help = "扫描 database_config，将明文密码加密后写回（已加密的跳过）"

    def handle(self, *args, **options):
        updated = 0
        for cfg in DatabaseConfig.objects.iterator():
            if not cfg.password:
                continue
            if is_encrypted(cfg.password):
                continue
            plain = cfg.password
            cfg.set_password(plain)
            cfg.save(update_fields=["password"])
            updated += 1
            self.stdout.write(f"已加密: {cfg.name} (id={cfg.pk})")

        self.stdout.write(self.style.SUCCESS(f"完成，新加密 {updated} 条配置"))
