# -*- coding: utf-8 -*-
"""
Django management command to initialize TimescaleDB hypertable.
Usage: python manage.py init_timeseries
"""

from django.core.management.base import BaseCommand
from monitor.timeseries import create_hypertable, drop_hypertable


class Command(BaseCommand):
    help = 'Initialize TimescaleDB hypertable for time-series data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--drop',
            action='store_true',
            help='Drop existing hypertable before creating',
        )
        parser.add_argument(
            '--recreate',
            action='store_true',
            help='Drop and recreate the hypertable',
        )

    def handle(self, *args, **options):
        if options['drop'] or options['recreate']:
            self.stdout.write('Dropping existing hypertable...')
            if drop_hypertable():
                self.stdout.write(self.style.SUCCESS('Hypertable dropped successfully'))
            else:
                self.stdout.write(self.style.ERROR('Failed to drop hypertable'))
                return

        self.stdout.write('Creating TimescaleDB hypertable...')
        if create_hypertable():
            self.stdout.write(self.style.SUCCESS('Hypertable created successfully'))
            self.stdout.write(self.style.SUCCESS('TimescaleDB is ready for time-series data'))
        else:
            self.stdout.write(self.style.ERROR('Failed to create hypertable'))
            self.stdout.write(self.style.WARNING('Make sure TimescaleDB is installed and accessible'))
