import time

from django.core.management.base import BaseCommand

from grading.services.batch_jobs import claim_next_queued_job, run_batch_review_job
from grading.services.cohort_summary_jobs import claim_next_queued_cohort_summary_job, run_cohort_summary_job


class Command(BaseCommand):
    help = "Process queued background jobs (batch review and cohort summary)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=2.0,
            help="Seconds to wait when no queued jobs exist.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one queued job, then exit.",
        )

    def handle(self, *args, **options):
        poll_interval = max(0.1, options["poll_interval"])
        run_once = options["once"]

        while True:
            batch_job = claim_next_queued_job()
            cohort_job = None
            if not batch_job:
                cohort_job = claim_next_queued_cohort_summary_job()

            if not batch_job and not cohort_job:
                if run_once:
                    self.stdout.write(self.style.SUCCESS("No queued jobs."))
                    return
                time.sleep(poll_interval)
                continue

            if batch_job:
                self.stdout.write(f"Processing batch job #{batch_job.pk} for assignment #{batch_job.assignment_id}")
                run_batch_review_job(batch_job.pk)
            else:
                self.stdout.write(
                    f"Processing cohort summary job #{cohort_job.pk} for assignment #{cohort_job.assignment_id}"
                )
                run_cohort_summary_job(cohort_job.pk)

            if run_once:
                return
