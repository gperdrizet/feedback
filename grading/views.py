from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from grading.models import AssignmentConfig, SubmissionRecord
from grading.services.canvas_sync import (
	approve_submission,
	generate_ai_draft,
	post_submission_to_canvas,
	sync_assignment as sync_assignment_from_canvas,
)


def gradebook(request):
	assignments = AssignmentConfig.objects.select_related("course").prefetch_related("submissions")
	return render(request, "grading/gradebook.html", {"assignments": assignments})


def sync_assignment(request):
	if request.method != "POST":
		return redirect("grading:gradebook")

	course_id = request.POST.get("course_id")
	assignment_id = request.POST.get("assignment_id")

	if not course_id or not assignment_id:
		messages.error(request, "Course ID and Assignment ID are required.")
		return redirect("grading:gradebook")

	try:
		assignment = sync_assignment_from_canvas(
			course_id=int(course_id),
			assignment_id=int(assignment_id),
			download_root="submissions",
		)
		messages.success(request, f"Synced assignment: {assignment.name}")
	except Exception as exc:  # noqa: BLE001
		messages.error(request, f"Sync failed: {exc}")

	return redirect("grading:gradebook")


def assignment_detail(request, assignment_pk):
	assignment = get_object_or_404(AssignmentConfig.objects.select_related("course"), pk=assignment_pk)
	submissions = assignment.submissions.all().order_by("student_name")
	return render(
		request,
		"grading/assignment_detail.html",
		{"assignment": assignment, "submissions": submissions},
	)


def submission_detail(request, submission_pk):
	submission = get_object_or_404(
		SubmissionRecord.objects.select_related("assignment", "assignment__course").prefetch_related(
			"artifacts",
			"ai_drafts",
			"approval_decisions",
			"post_attempts",
		),
		pk=submission_pk,
	)

	if request.method == "POST":
		action = request.POST.get("action")
		try:
			if action == "generate":
				generate_ai_draft(submission)
				messages.success(request, "AI draft generated.")
			elif action == "approve":
				submission.final_feedback = request.POST.get("final_feedback", submission.proposed_feedback)
				final_score_raw = request.POST.get("final_score", "").strip()
				submission.final_score = final_score_raw or submission.proposed_score
				submission.save(update_fields=["final_feedback", "final_score"])
				approve_submission(
					submission,
					instructor_username=request.user.username if request.user.is_authenticated else "local-admin",
					notes=request.POST.get("notes", ""),
				)
				messages.success(request, "Submission approved and queued for posting.")
			elif action == "post":
				post_submission_to_canvas(submission)
				messages.success(request, "Submission posted to Canvas.")
		except Exception as exc:  # noqa: BLE001
			messages.error(request, f"Action failed: {exc}")

		return redirect("grading:submission_detail", submission_pk=submission.pk)

	return render(request, "grading/submission_detail.html", {"submission": submission})

