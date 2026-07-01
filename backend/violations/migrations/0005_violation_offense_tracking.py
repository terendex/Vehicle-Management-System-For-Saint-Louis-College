# Generated for violation offense tracking system

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0004_violation_issued_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='violation',
            name='offense_number',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='violation',
            name='status',
            field=models.CharField(
                choices=[
                    ('warning', 'Warning'),
                    ('fee_imposed', 'Fee Imposed'),
                    ('cleared', 'Cleared'),
                ],
                default='warning',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='violation',
            name='registration_blocked',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='violation',
            name='cdso_report_issued',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='violation',
            name='official_receipt',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='violation',
            name='violation_type',
            field=models.CharField(
                choices=[
                    ('unauthorized_entry',   'Unauthorized Entry'),
                    ('double_parking',       'Double Parking'),
                    ('time_exceed',          'Time Exceed'),
                    ('no_sticker',           'No Sticker'),
                    ('expired_registration', 'Expired Registration'),
                    ('unauthorized',         'Unauthorized (Legacy)'),
                    ('other',                'Other'),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='violation',
            name='fine_amount',
            field=models.DecimalField(decimal_places=2, default='0.00', max_digits=6),
        ),
    ]
