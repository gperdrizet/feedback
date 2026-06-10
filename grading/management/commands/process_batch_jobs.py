import time

from django.core.management.base import BaseCommand

from grading.services.batch_jobs import claim_next_queued_job, run_batch_review_job


class Command(BaseCommand):
    help = "Process queued batch review jobs."

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
            job = claim_next_queued_job()
            if not job:
                if run_once:
                    self.stdout.write(self.style.SUCCESS("No queued jobs."))
                    return
                time.sleep(poll_interval)
                continue

            self.stdout.write(f"Processing batch job #{job.pk} for assignment #{job.assignment_id}")
            run_batch_review_job(job.pk)

            if run_once:
                return
