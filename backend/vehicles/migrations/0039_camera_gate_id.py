from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0038_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='camera',
            name='gate_id',
            field=models.CharField(
                blank=True,
                choices=[('gate1', 'Gate 1'), ('gate4', 'Gate 4')],
                help_text='Required when assignment is Entry. Identifies which gate this camera covers.',
                max_length=10,
                null=True,
            ),
        ),
    ]
