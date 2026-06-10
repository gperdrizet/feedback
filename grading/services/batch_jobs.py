"""Batch review queue helpers."""

from django.db import close_old_connections
from django.utils import timezone

from grading.models import AssignmentConfig, BatchReviewJob
from grading.services.canvas_sync import generate_ai_draft, sync_assignment


def enqueue_batch_review_job(assignment):
    running_job = assignment.batch_jobs.filter(
        status__in=[BatchReviewJob.Status.QUEUED, BatchReviewJob.Status.RUNNING]
    ).order_by("-created_at").first()
    if running_job:
        return running_job, False

    job = BatchReviewJob.objects.create(assignment=assignment, status=BatchReviewJob.Status.QUEUED)
    return job, True


def claim_next_queued_job():
    job = BatchReviewJob.objects.select_related("assignment", "assignment__course").filter(
        status=BatchReviewJob.Status.QUEUED
    ).order_by("created_at").first()
    if not job:
        return None

    updated = BatchReviewJob.objects.filter(
        pk=job.pk,
        status=BatchReviewJob.Status.QUEUED,
    ).update(
        status=BatchReviewJob.Status.RUNNING,
        started_at=timezone.now(),
        finished_at=None,
        summary_message="",
        last_error="",
        current_student_name="",
        total_submissions=0,
        completed_submissions=0,
        failed_submissions=0,
    )
    if not updated:
        return None

    return BatchReviewJob.objects.select_related("assignment", "assignment__course").get(pk=job.pk)


def _ordered_assignment_submissions(assignment):
    latest_by_user = {}
    for submission in assignment.submissions.all():
        current = latest_by_user.get(submission.canvas_user_id)
        if current is None:
            latest_by_user[submission.canvas_user_id] = submission
            continue

        if submission.submitted_at and current.submitted_at:
            if submission.submitted_at > current.submitted_at:
                latest_by_user[submission.canvas_user_id] = submission
                continue
            if submission.submitted_at < current.submitted_at:
                continue
        elif submission.submitted_at and not current.submitted_at:
            latest_by_user[submission.canvas_user_id] = submission
            continue
        elif not submission.submitted_at and current.submitted_at:
            continue

        if submission.canvas_submission_id > current.canvas_submission_id:
            latest_by_user[submission.canvas_user_id] = submission

    return sorted(
        latest_by_user.values(),
        key=lambda item: (item.student_name.lower(), item.canvas_submission_id),
    )


def run_batch_review_job(job_pk):
    close_old_connections()
    try:
        job = BatchReviewJob.objects.select_related("assignment", "assignment__course").get(pk=job_pk)

        sync_assignment(
            course_id=job.assignment.course.canvas_course_id,
            assignment_id=job.assignment.canvas_assignment_id,
            download_root="submissions",
        )

        assignment = AssignmentConfig.objects.select_related("course").get(pk=job.assignment.pk)
        submissions = list(_ordered_assignment_submissions(assignment))
        job.total_submissions = len(submissions)
        job.save(update_fields=["total_submissions"])

        for submission in submissions:
            job.current_student_name = submission.student_name
            job.save(update_fields=["current_student_name"])
            try:
                generate_ai_draft(submission)
                job.completed_submissions += 1
            except Exception as exc:  # noqa: BLE001
                job.failed_submissions += 1
                job.last_error = f"{submission.student_name}: {exc}"
            finally:
                job.save(update_fields=["completed_submissions", "failed_submissions", "last_error"])

        job.status = BatchReviewJob.Status.COMPLETED
        job.finished_at = timezone.now()
        job.current_student_name = ""
        job.summary_message = (
            f"Generated drafts for {job.completed_submissions} of {job.total_submissions} submissions."
            + (f" {job.failed_submissions} failed." if job.failed_submissions else "")
        )
        job.save(update_fields=["status", "finished_at", "current_student_name", "summary_message"])
    except Exception as exc:  # noqa: BLE001
        BatchReviewJob.objects.filter(pk=job_pk).update(
            status=BatchReviewJob.Status.FAILED,
            finished_at=timezone.now(),
            current_student_name="",
            last_error=str(exc),
            summary_message="Batch review failed.",
        )
    finally:
        close_old_connections()
