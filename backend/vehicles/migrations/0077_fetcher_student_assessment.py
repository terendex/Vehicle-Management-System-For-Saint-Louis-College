import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0076_registration_payment_tracking'),
    ]

    operations = [
        migrations.CreateModel(
            name='FetcherStudentAssessment',
            fields=[
                ('id', models.BigAutoField(
                    primary_key=True, serialize=False,
                    db_column='fetcher_student_assessment_id',
                )),
                ('student_index', models.PositiveIntegerField()),
                ('assessment_form', models.FileField(
                    upload_to='assessments/fetcher/',
                    validators=[django.core.validators.FileExtensionValidator(
                        allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'pdf'],
                    )],
                )),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('registration', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='fetcher_assessments',
                    to='vehicles.vehicleregistration',
                )),
            ],
            options={
                'db_table': 'tbl_fetcher_student_assessment',
                'ordering': ['student_index'],
            },
        ),
        migrations.AddConstraint(
            model_name='fetcherstudentassessment',
            constraint=models.UniqueConstraint(
                fields=('registration', 'student_index'),
                name='uniq_fetcher_assessment_per_student',
            ),
        ),
    ]
