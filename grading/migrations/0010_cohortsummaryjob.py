import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0009_aifeedbackdraft_prompt_diagnostics"),
    ]

    operations = [
        migrations.CreateModel(
            name="CohortSummaryJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("completed", "Completed"), ("failed", "Failed")], default="queued", max_length=20)),
                ("summary_message", models.TextField(blank=True)),
                ("last_error", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("assignment", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="cohort_summary_jobs", to="grading.assignmentconfig")),
            ],
        ),
    ]
