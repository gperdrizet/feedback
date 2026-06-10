from django.utils import timezone

from grading.services.ai_provider import OpenAICompatibleProvider


def _ordered_latest_submissions(assignment):
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


def _feedback_entries_for_assignment(assignment):
    rows = []
    for submission in _ordered_latest_submissions(assignment):
        feedback = (submission.final_feedback or submission.proposed_feedback or "").strip()
        if not feedback:
            continue
        score = submission.final_score if submission.final_score is not None else submission.proposed_score
        rows.append(
            {
                "student_name": submission.student_name,
                "review_status": submission.review_status,
                "score": str(score) if score is not None else None,
                "feedback": feedback,
            }
        )
    return rows


def generate_assignment_cohort_summary(assignment):
    feedback_entries = _feedback_entries_for_assignment(assignment)
    if not feedback_entries:
        raise ValueError("No generated feedback exists yet. Generate student drafts before creating a cohort summary.")

    provider = OpenAICompatibleProvider()
    summary_html = provider.generate_cohort_summary(
        assignment_name=assignment.name,
        assignment_description=assignment.assignment_description or "",
        extra_instructions=assignment.additional_instructions or "",
        feedback_entries=feedback_entries,
    )

    assignment.cohort_summary_html = summary_html
    assignment.cohort_summary_generated_at = timezone.now()
    assignment.cohort_summary_last_error = ""
    assignment.save(update_fields=["cohort_summary_html", "cohort_summary_generated_at", "cohort_summary_last_error"])
    return assignment
