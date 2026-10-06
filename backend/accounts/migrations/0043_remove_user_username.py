from django.db import migrations


class Migration(migrations.Migration):
    """Take username off the User model.

    Sign-in is by email and the name is last_name / first_name /
    middle_initial, so the field held nothing. Removed from Django's state
    only: the column stays, nullable, so a campus install still on the
    previous code (which selects every field) keeps working against the
    shared database. `manage.py drop_legacy_username --apply` drops it once
    every server runs this code.
    """

    dependencies = [
        ('accounts', '0042_relabel_fetcher_driver'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name='user', name='username'),
            ],
            database_operations=[],
        ),
    ]
