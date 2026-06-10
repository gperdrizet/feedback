from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0006_submissionrecord_model_adjustments_last_used_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignmentconfig",
            name="cohort_summary_generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="assignmentconfig",
            name="cohort_summary_html",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="assignmentconfig",
            name="cohort_summary_last_error",
            field=models.TextField(blank=True),
        ),
    ]
