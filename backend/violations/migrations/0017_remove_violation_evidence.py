"""Drop the violation evidence photo.

The gate camera, the parking camera and the visitor-exit sweeps each attached a
JPEG to every violation they raised. That capture is gone: nothing writes a
photo any more, nothing reads one, and the column has no callers left.

The image FILES are not touched. RemoveField drops the column that named them,
so whatever is already in the bucket stays there, unreferenced, for whoever
handles storage cleanup — a migration is the wrong place to delete from an
object store, because a failed deploy would roll the schema back and the files
would already be gone.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('violations', '0016_backfill_violation_owner'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='violation',
            name='evidence',
        ),
    ]
