import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0074_seed_missing_rule_constraints'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicleregistration',
            name='assessment_form',
            field=models.FileField(
                blank=True, null=True, upload_to='assessments/',
                validators=[django.core.validators.FileExtensionValidator(
                    allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf'],
                )],
            ),
        ),
    ]
