# "Other" as a supplier or scheduled-visit category now carries the text that
# says what it means. Nullable on purpose: the campus install and Railway share
# one database, and a server still on older code inserts without the column.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0090_scheduled_visit_archive'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='category_other',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='scheduledvisit',
            name='category_other',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
