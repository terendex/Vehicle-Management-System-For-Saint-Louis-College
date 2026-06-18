from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('vehicles', '0006_add_system_ids_to_registration'),
    ]

    operations = [
migrations.AddField(
            model_name='owner',
            name='user_code',
            field=models.CharField(blank=True, db_index=True, help_text='SLC user code (e.g., SLC-OWN-000001) linking to accounts.User', max_length=20),
        ),
    ]