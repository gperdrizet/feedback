from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0008_batchreviewjob_generation_modes"),
    ]

    operations = [
        migrations.AddField(
            model_name="aifeedbackdraft",
            name="prompt_diagnostics",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
