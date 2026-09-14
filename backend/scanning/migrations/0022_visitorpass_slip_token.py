from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0021_visitorpass_visitor_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='visitorpass',
            name='slip_token',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
    ]
