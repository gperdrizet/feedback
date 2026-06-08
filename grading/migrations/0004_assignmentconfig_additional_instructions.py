from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0003_rubric"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignmentconfig",
            name="additional_instructions",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
