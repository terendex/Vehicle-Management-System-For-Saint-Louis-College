# The CDSO's edited wording of the Policies page tabs. Written by hand so it
# carries only this table.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0095_relabel_fetcher_parent'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PolicyDocument',
            fields=[
                ('id', models.BigAutoField(db_column='policy_document_id', primary_key=True, serialize=False)),
                ('key', models.CharField(choices=[('privacy', 'Privacy Policy'), ('terms', 'Vehicle Pass Terms')], max_length=20, unique=True)),
                ('content', models.TextField()),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'tbl_policy_document',
            },
        ),
    ]
