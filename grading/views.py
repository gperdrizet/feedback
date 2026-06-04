from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from grading.models import AssignmentConfig, SubmissionRecord
from grading.services.canvas_sync import (
	approve_submission,
	generate_ai_draft,
	post_submission_to_canvas,
	sync_assignment as sync_assignment_from_canvas,
)


def _ordered_assignment_submissions(assignment):
	return assignment.submissions.all().order_by("student_name", "canvas_submission_id")


def _neighboring_submission_ids(submissions, current_submission_id):
	submission_ids = [submission.pk for submission in submissions]
	try:
		current_index = submission_ids.index(current_submission_id)
	except ValueError:
		return None, None

	previous_id = submission_ids[current_index - 1] if current_index > 0 else None
	next_id = submission_ids[current_index + 1] if current_index < len(submission_ids) - 1 else None
	return previous_id, next_id


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
	assignment = get_object_or_404(
		AssignmentConfig.objects.select_related("course").prefetch_related("submissions"),
		pk=assignment_pk,
	)

	if request.method == "POST":
		action = request.POST.get("action")
		if action == "batch_review":
			try:
				sync_assignment_from_canvas(
					course_id=assignment.course.canvas_course_id,
					assignment_id=assignment.canvas_assignment_id,
					download_root="submissions",
				)

				refreshed_assignment = get_object_or_404(
					AssignmentConfig.objects.select_related("course").prefetch_related("submissions"),
					pk=assignment_pk,
				)
				submissions = list(_ordered_assignment_submissions(refreshed_assignment))
				generated_count = 0
				failed_count = 0
				for submission in submissions:
					try:
						generate_ai_draft(submission)
						generated_count += 1
					except Exception as exc:  # noqa: BLE001
						failed_count += 1
						messages.error(request, f"{submission.student_name}: {exc}")

				messages.success(
					request,
					f"Downloaded submissions and generated AI drafts for {generated_count} students."
					+ (f" {failed_count} failed." if failed_count else ""),
				)
				if submissions:
					return redirect("grading:submission_detail", submission_pk=submissions[0].pk)
			except Exception as exc:  # noqa: BLE001
				messages.error(request, f"Batch review failed: {exc}")

		return redirect("grading:assignment_detail", assignment_pk=assignment.pk)

	submissions = _ordered_assignment_submissions(assignment)
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
	ordered_submissions = list(_ordered_assignment_submissions(submission.assignment))
	previous_submission_pk, next_submission_pk = _neighboring_submission_ids(
		ordered_submissions,
		submission.pk,
	)

	if request.method == "POST":
		action = request.POST.get("action")
		try:
			if action == "generate":
				generate_ai_draft(submission)
				messages.success(request, "AI draft generated.")
			elif action == "save":
				submission.final_feedback = request.POST.get("final_feedback", submission.final_feedback)
				final_score_raw = request.POST.get("final_score", "").strip()
				submission.final_score = final_score_raw or submission.final_score or submission.proposed_score
				submission.save(update_fields=["final_feedback", "final_score"])
				messages.success(request, "Review saved.")
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
				submission.final_feedback = request.POST.get("final_feedback", submission.final_feedback or submission.proposed_feedback)
				final_score_raw = request.POST.get("final_score", "").strip()
				submission.final_score = final_score_raw or submission.final_score or submission.proposed_score
				submission.save(update_fields=["final_feedback", "final_score"])
				approve_submission(
					submission,
					instructor_username=request.user.username if request.user.is_authenticated else "local-admin",
					notes=request.POST.get("notes", ""),
				)
				post_submission_to_canvas(submission)
				messages.success(request, "Submission posted to Canvas.")
		except Exception as exc:  # noqa: BLE001
			messages.error(request, f"Action failed: {exc}")

		return redirect("grading:submission_detail", submission_pk=submission.pk)

	return render(
		request,
		"grading/submission_detail.html",
		{
			"submission": submission,
			"ordered_submissions": ordered_submissions,
			"previous_submission_pk": previous_submission_pk,
			"next_submission_pk": next_submission_pk,
		},
	)

