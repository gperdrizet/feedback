from django.db import close_old_connections
from django.utils import timezone

from grading.models import AssignmentConfig, CohortSummaryJob
from grading.services.cohort_summary import generate_assignment_cohort_summary


def enqueue_cohort_summary_job(assignment):
    running_job = assignment.cohort_summary_jobs.filter(
        status__in=[CohortSummaryJob.Status.QUEUED, CohortSummaryJob.Status.RUNNING]
    ).order_by("-created_at").first()
    if running_job:
        return running_job, False

    job = CohortSummaryJob.objects.create(
        assignment=assignment,
        status=CohortSummaryJob.Status.QUEUED,
    )
    return job, True


def claim_next_queued_cohort_summary_job():
    job = CohortSummaryJob.objects.select_related("assignment").filter(
        status=CohortSummaryJob.Status.QUEUED
    ).order_by("created_at").first()
    if not job:
        return None

    updated = CohortSummaryJob.objects.filter(
        pk=job.pk,
        status=CohortSummaryJob.Status.QUEUED,
    ).update(
        status=CohortSummaryJob.Status.RUNNING,
        started_at=timezone.now(),
        finished_at=None,
        summary_message="",
        last_error="",
    )
    if not updated:
        return None

    return CohortSummaryJob.objects.select_related("assignment").get(pk=job.pk)


def run_cohort_summary_job(job_pk):
    close_old_connections()
    try:
        job = CohortSummaryJob.objects.select_related("assignment").get(pk=job_pk)
        assignment = AssignmentConfig.objects.get(pk=job.assignment.pk)

        generate_assignment_cohort_summary(assignment)

        job.status = CohortSummaryJob.Status.COMPLETED
        job.finished_at = timezone.now()
        job.summary_message = "Cohort summary generated successfully."
        job.last_error = ""
        job.save(update_fields=["status", "finished_at", "summary_message", "last_error"])
    except Exception as exc:  # noqa: BLE001
        CohortSummaryJob.objects.filter(pk=job_pk).update(
            status=CohortSummaryJob.Status.FAILED,
            finished_at=timezone.now(),
            last_error=str(exc),
            summary_message="Cohort summary generation failed.",
        )
        assignment_id = CohortSummaryJob.objects.filter(pk=job_pk).values_list("assignment_id", flat=True).first()
        if assignment_id:
            AssignmentConfig.objects.filter(pk=assignment_id).update(cohort_summary_last_error=str(exc))
    finally:
        close_old_connections()
