from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0002_batchreviewjob"),
    ]

    operations = [
        migrations.CreateModel(
            name="RubricCriterion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=500)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rubric_criteria",
                        to="grading.assignmentconfig",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "pk"],
            },
        ),
        migrations.CreateModel(
            name="RubricLevel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("points", models.DecimalField(decimal_places=2, max_digits=8)),
                ("description", models.TextField(blank=True)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                (
                    "criterion",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="levels",
                        to="grading.rubriccriterion",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "-points"],
            },
        ),
    ]
