import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import markdown as markdown_lib
import requests
from django.contrib import messages
from django.utils.safestring import mark_safe
from django.shortcuts import get_object_or_404, redirect, render
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.util import ClassNotFound

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


def _coerce_submission_sources(submission):
	sources = []
	for artifact in submission.artifacts.all():
		if artifact.local_path:
			sources.append(("local_path", artifact.local_path))
		if artifact.source_url:
			sources.append(("source_url", artifact.source_url))

	if submission.submission_url:
		sources.append(("submission_url", submission.submission_url))

	return sources


def _is_notebook_reference(value):
	if not value:
		return False

	parsed = urlparse(value)
	path = parsed.path if parsed.scheme else value
	return path.lower().endswith(".ipynb")


_PYGMENTS_CSS = HtmlFormatter(style="default").get_style_defs(".highlight")


def _coerce_cell_source(source):
	if isinstance(source, list):
		return "".join(source)
	if source is None:
		return ""
	return str(source)


def _render_markdown_cell(text):
	if not text.strip():
		return mark_safe("<p><em>Empty markdown cell</em></p>")
	return mark_safe(markdown_lib.markdown(text, extensions=["fenced_code", "tables", "nl2br"]))


def _render_code_cell(text, language=None):
	try:
		lexer = get_lexer_by_name(language) if language else guess_lexer(text)
	except ClassNotFound:
		lexer = TextLexer()
	return mark_safe(highlight(text, lexer, HtmlFormatter(style="default")))


def _render_cell_outputs(outputs):
	parts = []
	for output in outputs or []:
		output_type = output.get("output_type", "")
		if output_type in ("stream", "execute_result", "display_data"):
			data = output.get("data") or {}
			# PNG image (base64 encoded)
			png = data.get("image/png") or output.get("image/png")
			if png:
				raw = png if isinstance(png, str) else "".join(png)
				parts.append(f'<img src="data:image/png;base64,{raw.strip()}" style="max-width:100%;">')
				continue
			# HTML output
			html_out = data.get("text/html")
			if html_out:
				text = html_out if isinstance(html_out, str) else "".join(html_out)
				parts.append(f'<div class="nb-output-html">{text}</div>')
				continue
			# Plain text / stdout
			text_out = data.get("text/plain") or output.get("text")
			if text_out:
				text = text_out if isinstance(text_out, str) else "".join(text_out)
				parts.append(f'<pre class="nb-output">{text}</pre>')
				continue
		elif output_type == "error":
			traceback_lines = output.get("traceback", [output.get("evalue", "")])
			raw = "\n".join(traceback_lines) if isinstance(traceback_lines, list) else traceback_lines
			parts.append(f'<pre class="nb-output nb-output-error">{raw}</pre>')
	return mark_safe("\n".join(parts)) if parts else None


def _load_notebook_payload(source_kind, source_value):
	if source_kind == "local_path":
		path = Path(source_value)
		if not path.exists() or path.is_dir():
			return None
		return json.loads(path.read_text(encoding="utf-8"))

	response = requests.get(source_value, timeout=30)
	response.raise_for_status()
	return response.json()


def _build_notebook_preview(submission, max_cells=60):
	for source_kind, source_value in _coerce_submission_sources(submission):
		if not _is_notebook_reference(source_value):
			continue

		try:
			payload = _load_notebook_payload(source_kind, source_value)
		except (OSError, ValueError, json.JSONDecodeError, requests.RequestException):
			continue

		if not isinstance(payload, dict):
			continue

		kernel_language = (
			payload.get("metadata", {}).get("kernelspec", {}).get("language")
			or payload.get("metadata", {}).get("language_info", {}).get("name")
		)

		cells = []
		raw_cells = payload.get("cells", []) or []
		for cell in raw_cells[:max_cells]:
			cell_type = cell.get("cell_type", "unknown")
			source = _coerce_cell_source(cell.get("source"))
			cell_language = (
				cell.get("metadata", {}).get("language")
				or kernel_language
			)
			if cell_type == "markdown":
				rendered_html = _render_markdown_cell(source)
				output_html = None
			else:
				rendered_html = _render_code_cell(source, language=cell_language)
				output_html = _render_cell_outputs(cell.get("outputs"))

			cells.append(
				{
					"cell_type": cell_type,
					"title": cell_language or cell_type.title(),
					"rendered_html": rendered_html,
					"output_html": output_html,
				}
			)

		return {
			"source": source_value,
			"cell_count": len(raw_cells),
			"truncated": len(raw_cells) > len(cells),
			"cells": cells,
			"pygments_css": _PYGMENTS_CSS,
		}

	return None


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
			"notebook_preview": _build_notebook_preview(submission),
		},
	)

