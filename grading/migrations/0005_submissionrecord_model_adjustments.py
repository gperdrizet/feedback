from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0004_assignmentconfig_additional_instructions"),
    ]

    operations = [
        migrations.AddField(
            model_name="submissionrecord",
            name="model_adjustments",
            field=models.TextField(blank=True),
        ),
    ]
