from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0005_submissionrecord_model_adjustments"),
    ]

    operations = [
        migrations.AddField(
            model_name="submissionrecord",
            name="model_adjustments_last_used_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
