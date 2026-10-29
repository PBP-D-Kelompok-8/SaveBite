from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from request_board.models import RequestCreationKey


class Command(BaseCommand):
    help = 'Delete creation keys older than the 30-day retry retention window.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Count expired keys without deleting them.')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        expired = RequestCreationKey.objects.filter(created_at__lt=cutoff)
        if options['dry_run']:
            self.stdout.write(f'{expired.count()} creation keys eligible for deletion.')
            return
        deleted = 0
        while True:
            ids = list(expired.order_by('created_at', 'pk').values_list('pk', flat=True)[:500])
            if not ids:
                break
            count, _ = RequestCreationKey.objects.filter(pk__in=ids).delete()
            deleted += count
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} expired creation keys.'))
