from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('request_board', '0005_requestcreationkey')]

    operations = [
        migrations.AddIndex(
            model_name='requestcreationkey',
            index=models.Index(fields=['created_at'], name='request_key_created_idx'),
        ),
    ]
