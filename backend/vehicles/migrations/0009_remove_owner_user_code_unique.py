from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('vehicles', '0008_backfill_owner_user_code'),
    ]

    operations = [
        migrations.AlterField(
            model_name='owner',
            name='user_code',
            field=models.CharField(blank=True, db_index=True, help_text='SLC user code (e.g., SLC-OWN-000001) linking to accounts.User', max_length=20),
        ),
    ]

    operations = [
        migrations.AlterField(
            model_name='owner',
            name='user_code',
            field=models.CharField(blank=True, db_index=True, help_text='SLC user code (e.g., SLC-OWN-000001) linking to accounts.User', max_length=20),
        ),
    ]