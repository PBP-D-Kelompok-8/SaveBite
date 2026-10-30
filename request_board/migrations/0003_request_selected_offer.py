from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('request_board', '0002_requestoffer')]

    operations = [
        migrations.AddField(
            model_name='request',
            name='selected_offer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='selected_for_requests', to='request_board.requestoffer'),
        ),
    ]
