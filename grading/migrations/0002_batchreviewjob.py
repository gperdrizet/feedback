from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BatchReviewJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=20,
                    ),
                ),
                ("total_submissions", models.PositiveIntegerField(default=0)),
                ("completed_submissions", models.PositiveIntegerField(default=0)),
                ("failed_submissions", models.PositiveIntegerField(default=0)),
                ("current_student_name", models.CharField(blank=True, max_length=255)),
                ("summary_message", models.TextField(blank=True)),
                ("last_error", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="batch_jobs",
                        to="grading.assignmentconfig",
                    ),
                ),
            ],
        ),
    ]
