from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0007_assignmentconfig_cohort_summary_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="batchreviewjob",
            name="use_detailed_passes",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="batchreviewjob",
            name="use_review_pass",
            field=models.BooleanField(default=False),
        ),
    ]
