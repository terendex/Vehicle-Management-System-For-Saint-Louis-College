import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0022_visitorpass_slip_token'),
        ('vehicles', '0083_parkingzone_baseline_by_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslog',
            name='event',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='access_logs', to='vehicles.event',
            ),
        ),
        migrations.AlterField(
            model_name='accesslog',
            name='entrant_category',
            field=models.CharField(
                blank=True, default='', max_length=20,
                choices=[('student', 'Student'), ('employee', 'Employee'),
                         ('fetcher', 'Fetcher / Drop & Go'), ('visitor', 'Visitor'),
                         ('supplier', 'Supplier'), ('event', 'Event Organizer'),
                         ('unknown', 'Unregistered')],
            ),
        ),
    ]
