
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('food_rescue_planner', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='foodrescueplan',
            old_name='action_method',
            new_name='metode_aksi',
        ),
        migrations.RenameField(
            model_name='foodrescueplan',
            old_name='food_name',
            new_name='nama_bahan',
        ),
        migrations.RenameField(
            model_name='foodrescueplan',
            old_name='quantity',
            new_name='jumlah',
        ),
        migrations.AlterField(
            model_name='foodrescueplan',
            name='jumlah',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RenameField(
            model_name='foodrescueplan',
            old_name='unit',
            new_name='satuan',
        ),
        migrations.AlterField(
            model_name='foodrescueplan',
            name='satuan',
            field=models.CharField(
                choices=[
                    ('kg', 'Kilogram'),
                    ('gr', 'Gram'),
                    ('pcs', 'Pieces'),
                    ('porsi', 'Porsi'),
                    ('liter', 'Liter'),
                ],
                default='pcs',
                max_length=20,
            ),
        ),
    ]
