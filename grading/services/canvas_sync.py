"""Canvas sync services that consume canvas-instructor-tools APIs."""

from datetime import datetime
from pathlib import Path

import requests
from django.utils import timezone

try:
    from canvas_tools import post_submission_grade  # type: ignore[attr-defined]
except ImportError:
    from canvas_tools.client import get_client as _legacy_get_client

    def post_submission_grade(course_id, assignment_id, user_id, posted_grade, comment=None):
        canvas = _legacy_get_client()
        course = canvas.get_course(course_id)
        assignment = course.get_assignment(assignment_id)
        submission = assignment.get_submission(user_id)

        payload = {"submission": {"posted_grade": posted_grade}}
        if comment:
            payload["comment"] = {"text_comment": comment}

        updated = submission.edit(**payload)
        return {
            "success": True,
            "course_id": course_id,
            "assignment_id": assignment_id,
            "user_id": user_id,
            "posted_grade": posted_grade,
            "comment": comment,
            "canvas_response": updated,
        }

try:
    from canvas_tools import (  # type: ignore[attr-defined]
        download_submission_artifacts,
        get_assignment_description,
        list_assignment_submissions,
    )
except ImportError:
    from canvas_tools.client import get_client

    def list_assignment_submissions(course_id, assignment_id, include_history=True):
        canvas = get_client()
        course = canvas.get_course(course_id)
        assignment = course.get_assignment(assignment_id)
        include = ["user"]
        if include_history:
            include.append("submission_history")

        payloads = []
        for submission in assignment.get_submissions(include=include):
            user = getattr(submission, "user", None)
            attachments = []
            for attachment in getattr(submission, "attachments", []) or []:
                attachments.append(
                    {
                        "display_name": getattr(attachment, "display_name", None),
                        "url": getattr(attachment, "url", None),
                    }
                )

            payloads.append(
                {
                    "id": getattr(submission, "id", None),
                    "user_id": getattr(submission, "user_id", None),
                    "submission_type": getattr(submission, "submission_type", None),
                    "workflow_state": getattr(submission, "workflow_state", None),
                    "submitted_at": getattr(submission, "submitted_at", None),
                    "url": getattr(submission, "url", None),
                    "user": {
                        "id": user.get("id") if isinstance(user, dict) else None,
                        "name": user.get("name") if isinstance(user, dict) else None,
                    },
                    "attachments": attachments,
                }
            )

        return payloads

    def get_assignment_description(course_id, assignment_id):
        canvas = get_client()
        course = canvas.get_course(course_id)
        assignment = course.get_assignment(assignment_id)
        return {
            "id": assignment.id,
            "name": assignment.name,
            "description": getattr(assignment, "description", "") or "",
            "points_possible": getattr(assignment, "points_possible", None),
            "due_at": getattr(assignment, "due_at", None),
        }

    def _download_file(url, destination_path):
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with open(destination_path, "wb") as f:
            f.write(response.content)

    def _safe_filename(name):
        return (name or "artifact").replace("/", "-").replace(" ", "_")

    def download_submission_artifacts(course_id, assignment_id, output_dir=".", include_links=True):
        canvas = get_client()
        course = canvas.get_course(course_id)
        assignment = course.get_assignment(assignment_id)

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        result = {"downloaded_count": 0, "errors": [], "artifacts": []}
        submissions = assignment.get_submissions(include=["user", "submission_history"])

        for submission in submissions:
            user = getattr(submission, "user", None)
            user_name = "unknown_user"
            if isinstance(user, dict):
                user_name = _safe_filename(user.get("name", user_name))

            for attachment in getattr(submission, "attachments", []) or []:
                original_filename = getattr(attachment, "display_name", "attachment.bin")
                file_path = str(Path(output_dir) / f"{user_name}_{_safe_filename(original_filename)}")

                try:
                    _download_file(getattr(attachment, "url"), file_path)
                    result["downloaded_count"] += 1
                    result["artifacts"].append(
                        {
                            "submission_id": getattr(submission, "id", None),
                            "user_id": getattr(submission, "user_id", None),
                            "kind": "attachment",
                            "source_url": getattr(attachment, "url", None),
                            "local_path": file_path,
                        }
                    )
                except (requests.RequestException, OSError) as exc:
                    result["errors"].append({"submission_id": getattr(submission, "id", None), "error": str(exc)})

            if include_links and getattr(submission, "submission_type", None) == "online_url":
                submission_url = getattr(submission, "url", None)
                if submission_url:
                    link_path = str(Path(output_dir) / f"{user_name}_online_url")
                    try:
                        _download_file(submission_url, link_path)
                        result["downloaded_count"] += 1
                        result["artifacts"].append(
                            {
                                "submission_id": getattr(submission, "id", None),
                                "user_id": getattr(submission, "user_id", None),
                                "kind": "online_url",
                                "source_url": submission_url,
                                "local_path": link_path,
                            }
                        )
                    except (requests.RequestException, OSError) as exc:
                        result["errors"].append({"submission_id": getattr(submission, "id", None), "error": str(exc)})

        return result

from grading.models import (
    AIFeedbackDraft,
    ApprovalDecision,
    AssignmentConfig,
    CanvasPostAttempt,
    CourseSync,
    SubmissionArtifact,
    SubmissionRecord,
)
from grading.services.ai_provider import OpenAICompatibleProvider


def _parse_canvas_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def sync_assignment(course_id, assignment_id, download_root):
    assignment_meta = get_assignment_description(course_id, assignment_id)

    course, _ = CourseSync.objects.get_or_create(
        canvas_course_id=course_id,
        defaults={"name": f"Course {course_id}", "course_code": ""},
    )
    course.last_synced_at = timezone.now()
    course.save(update_fields=["last_synced_at"])

    assignment, _ = AssignmentConfig.objects.update_or_create(
        course=course,
        canvas_assignment_id=assignment_id,
        defaults={
            "name": assignment_meta["name"],
            "assignment_description": assignment_meta["description"],
            "points_possible": assignment_meta["points_possible"],
            "synced_at": timezone.now(),
        },
    )

    submissions = list_assignment_submissions(course_id, assignment_id)
    artifact_dir = Path(download_root) / f"course_{course_id}" / f"assignment_{assignment_id}"
    artifact_results = download_submission_artifacts(
        course_id=course_id,
        assignment_id=assignment_id,
        output_dir=str(artifact_dir),
        include_links=True,
    )

    records_by_canvas_submission_id = {}
    for payload in submissions:
        user_name = (payload.get("user") or {}).get("name") or f"User {payload.get('user_id')}"
        record, _ = SubmissionRecord.objects.update_or_create(
            assignment=assignment,
            canvas_submission_id=payload["id"],
            defaults={
                "canvas_user_id": payload.get("user_id") or 0,
                "student_name": user_name,
                "submission_type": payload.get("submission_type") or "",
                "submission_url": payload.get("url") or "",
                "submitted_at": _parse_canvas_datetime(payload.get("submitted_at")),
                "canvas_workflow_state": payload.get("workflow_state") or "",
                "synced_at": timezone.now(),
            },
        )
        record.artifacts.all().delete()
        records_by_canvas_submission_id[payload["id"]] = record

    for artifact in artifact_results["artifacts"]:
        submission = records_by_canvas_submission_id.get(artifact.get("submission_id"))
        if not submission:
            continue
        SubmissionArtifact.objects.create(
            submission=submission,
            artifact_type=artifact["kind"],
            source_url=artifact.get("source_url") or "",
            local_path=artifact.get("local_path") or "",
        )

    return assignment


def generate_ai_draft(submission):
    provider = OpenAICompatibleProvider()
    submission.ai_status = SubmissionRecord.AIStatus.PROCESSING
    submission.last_error = ""
    submission.save(update_fields=["ai_status", "last_error"])

    try:
        result = provider.generate_feedback(
            assignment_description=submission.assignment.assignment_description,
            student_name=submission.student_name,
            artifacts=list(submission.artifacts.all()),
        )
    except Exception as exc:  # noqa: BLE001
        submission.ai_status = SubmissionRecord.AIStatus.ERROR
        submission.last_error = str(exc)
        submission.save(update_fields=["ai_status", "last_error"])
        raise

    AIFeedbackDraft.objects.create(
        submission=submission,
        provider_name=result.provider_name,
        model_name=result.model_name,
        draft_feedback=result.feedback,
        draft_score=result.score,
    )

    submission.proposed_feedback = result.feedback
    submission.proposed_score = result.score
    submission.ai_status = SubmissionRecord.AIStatus.COMPLETE
    submission.review_status = SubmissionRecord.ReviewStatus.PENDING
    submission.save(
        update_fields=["proposed_feedback", "proposed_score", "ai_status", "review_status"]
    )


def approve_submission(submission, instructor_username, notes=""):
    submission.final_feedback = submission.final_feedback or submission.proposed_feedback
    submission.final_score = submission.final_score if submission.final_score is not None else submission.proposed_score
    submission.review_status = SubmissionRecord.ReviewStatus.APPROVED
    submission.save(update_fields=["final_feedback", "final_score", "review_status"])

    ApprovalDecision.objects.create(
        submission=submission,
        status=ApprovalDecision.DecisionStatus.APPROVED,
        instructor_username=instructor_username,
        notes=notes,
    )


def post_submission_to_canvas(submission):
    if submission.review_status != SubmissionRecord.ReviewStatus.APPROVED:
        raise ValueError("Submission must be approved before posting to Canvas.")

    if submission.final_score is None:
        raise ValueError("Submission needs a final score before posting to Canvas.")

    try:
        response = post_submission_grade(
            course_id=submission.assignment.course.canvas_course_id,
            assignment_id=submission.assignment.canvas_assignment_id,
            user_id=submission.canvas_user_id,
            posted_grade=str(submission.final_score),
            comment=submission.final_feedback or "",
        )
        CanvasPostAttempt.objects.create(
            submission=submission,
            success=True,
            response_payload=response,
        )
        submission.review_status = SubmissionRecord.ReviewStatus.POSTED
        submission.posted_at = timezone.now()
        submission.save(update_fields=["review_status", "posted_at"])
        return response
    except Exception as exc:  # noqa: BLE001
        CanvasPostAttempt.objects.create(
            submission=submission,
            success=False,
            error_message=str(exc),
        )
        submission.last_error = str(exc)
        submission.save(update_fields=["last_error"])
        raise
