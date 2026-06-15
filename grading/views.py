import json
from pathlib import Path
import re
from statistics import mean, median, pstdev
from urllib.parse import urlparse

import bleach
import markdown as markdown_lib
from django.contrib import messages
from django.utils.html import escape
from django.http import JsonResponse
from django.utils.safestring import mark_safe
from django.shortcuts import get_object_or_404, redirect, render
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.util import ClassNotFound

from grading.models import AssignmentConfig, BatchReviewJob, RubricCriterion, RubricLevel, SubmissionRecord
from grading.services.batch_jobs import enqueue_batch_review_job
from grading.services.canvas_sync import (
	approve_submission,
	generate_ai_draft,
	_is_unsubmitted_record,
	post_submission_to_canvas,
	sync_assignment as sync_assignment_from_canvas,
)
from grading.services.cohort_summary import generate_assignment_cohort_summary
from grading.services.http_safety import fetch_remote_text, get_max_preview_bytes


_ALLOWED_HTML_TAGS = [
	"a",
	"abbr",
	"acronym",
	"b",
	"blockquote",
	"br",
	"code",
	"em",
	"i",
	"li",
	"ol",
	"p",
	"pre",
	"strong",
	"u",
	"ul",
	"table",
	"thead",
	"tbody",
	"tr",
	"th",
	"td",
	"h1",
	"h2",
	"h3",
	"h4",
	"h5",
	"h6",
	"hr",
	"img",
	"span",
	"div",
]

_ALLOWED_HTML_ATTRIBUTES = {
	"a": ["href", "title", "rel", "target"],
	"img": ["src", "alt", "title"],
	"th": ["colspan", "rowspan"],
	"td": ["colspan", "rowspan"],
	"*": ["class"],
}

_ALLOWED_HTML_PROTOCOLS = ["http", "https", "mailto", "data"]
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")


def _sanitize_html_fragment(value):
	cleaned = bleach.clean(
		value or "",
		tags=_ALLOWED_HTML_TAGS,
		attributes=_ALLOWED_HTML_ATTRIBUTES,
		protocols=_ALLOWED_HTML_PROTOCOLS,
		strip=True,
	)
	return bleach.linkify(cleaned)


def _sanitize_feedback_html(value):
	return _sanitize_html_fragment(value)


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


def _serialize_submission_row(submission):
	return {
		"id": submission.pk,
		"ai_status": submission.ai_status,
		"review_status": submission.review_status,
		"proposed_score": str(submission.proposed_score) if submission.proposed_score is not None else "",
		"final_score": str(submission.final_score) if submission.final_score is not None else "",
	}


def _serialize_batch_job(job):
	if not job:
		return None

	return {
		"id": job.pk,
		"status": job.status,
		"use_detailed_passes": job.use_detailed_passes,
		"use_review_pass": job.use_review_pass,
		"total_submissions": job.total_submissions,
		"completed_submissions": job.completed_submissions,
		"failed_submissions": job.failed_submissions,
		"current_student_name": job.current_student_name,
		"summary_message": job.summary_message,
		"last_error": job.last_error,
		"started_at": job.started_at.isoformat() if job.started_at else None,
		"finished_at": job.finished_at.isoformat() if job.finished_at else None,
	}


def _start_batch_review_job(assignment):
	return enqueue_batch_review_job(assignment)


def _batch_mode_label(use_detailed_passes, use_review_pass):
	mode = "Detailed multi-pass" if use_detailed_passes else "Single-pass"
	if use_review_pass:
		return f"{mode} + refinement"
	return mode


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


def _artifact_display_name(artifact):
	if artifact.local_path:
		return Path(artifact.local_path).name
	if artifact.source_url:
		parsed = urlparse(artifact.source_url)
		name = Path(parsed.path).name
		return name or artifact.source_url
	return "artifact"


def _is_notebook_reference(value):
	if not value:
		return False

	parsed = urlparse(value)
	path = parsed.path if parsed.scheme else value
	return path.lower().endswith(".ipynb")


def _is_python_reference(value):
	if not value:
		return False

	parsed = urlparse(value)
	path = parsed.path if parsed.scheme else value
	return path.lower().endswith(".py")


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
	rendered = markdown_lib.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
	return mark_safe(_sanitize_html_fragment(rendered))


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
				raw = raw.strip()
				if _BASE64_RE.match(raw):
					compact = re.sub(r"\s+", "", raw)
					parts.append(f'<img src="data:image/png;base64,{compact}" alt="Notebook output image">')
				continue
			# HTML output
			html_out = data.get("text/html")
			if html_out:
				text = html_out if isinstance(html_out, str) else "".join(html_out)
				parts.append(f'<div class="nb-output-html">{_sanitize_html_fragment(text)}</div>')
				continue
			# Plain text / stdout
			text_out = data.get("text/plain") or output.get("text")
			if text_out:
				text = text_out if isinstance(text_out, str) else "".join(text_out)
				parts.append(f'<pre class="nb-output">{escape(text)}</pre>')
				continue
		elif output_type == "error":
			traceback_lines = output.get("traceback", [output.get("evalue", "")])
			raw = "\n".join(traceback_lines) if isinstance(traceback_lines, list) else traceback_lines
			parts.append(f'<pre class="nb-output nb-output-error">{escape(raw)}</pre>')
	return mark_safe("\n".join(parts)) if parts else None


def _read_local_text_with_limit(path):
	max_bytes = get_max_preview_bytes()
	if path.stat().st_size > max_bytes:
		raise ValueError(f"Preview file exceeds limit of {max_bytes} bytes")
	return path.read_text(encoding="utf-8", errors="ignore")


def _load_notebook_payload(source_kind, source_value):
	if source_kind == "local_path":
		path = Path(source_value)
		if not path.exists() or path.is_dir():
			return None
		return json.loads(_read_local_text_with_limit(path))

	return json.loads(fetch_remote_text(source_value, max_bytes=get_max_preview_bytes()))


def _load_text_payload(source_kind, source_value):
	if source_kind == "local_path":
		path = Path(source_value)
		if not path.exists() or path.is_dir():
			return None
		return _read_local_text_with_limit(path)

	return fetch_remote_text(source_value, max_bytes=get_max_preview_bytes())


def _build_notebook_preview(submission, max_cells=60):
	for source_kind, source_value in _coerce_submission_sources(submission):
		if not _is_notebook_reference(source_value):
			continue

		try:
			payload = _load_notebook_payload(source_kind, source_value)
		except (OSError, ValueError, json.JSONDecodeError):
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
			"kind": "notebook",
			"title": "Submission",
			"source": source_value,
			"cell_count": len(raw_cells),
			"truncated": len(raw_cells) > len(cells),
			"cells": cells,
			"pygments_css": _PYGMENTS_CSS,
		}

	return None


def _build_python_preview(submission):
	for source_kind, source_value in _coerce_submission_sources(submission):
		if not _is_python_reference(source_value):
			continue

		try:
			code = _load_text_payload(source_kind, source_value)
		except (OSError, ValueError):
			continue

		if code is None:
			continue

		return {
			"kind": "script",
			"title": "Submission",
			"source": source_value,
			"cell_count": 1,
			"truncated": False,
			"cells": [
				{
					"cell_type": "code",
					"title": "Python",
					"rendered_html": _render_code_cell(code, language="python"),
					"output_html": None,
				}
			],
			"pygments_css": _PYGMENTS_CSS,
		}

	return None


def _build_submission_preview(submission):
	return _build_notebook_preview(submission) or _build_python_preview(submission)


def _proposed_score_distribution(submissions, bin_count=10):
	scores = []
	for item in submissions:
		if item.proposed_score is None:
			continue
		try:
			scores.append(float(item.proposed_score))
		except (TypeError, ValueError):
			continue

	if not scores:
		return None

	sorted_scores = sorted(scores)
	min_score = sorted_scores[0]
	max_score = sorted_scores[-1]
	count = len(sorted_scores)
	avg = mean(sorted_scores)
	med = median(sorted_scores)
	std = pstdev(sorted_scores) if count > 1 else 0.0

	if min_score == max_score:
		bins = [
			{
				"label": f"{min_score:.2f}",
				"count": count,
			}
		]
		max_bin_count = count
	else:
		width = (max_score - min_score) / bin_count
		bins = []
		max_bin_count = 0
		for idx in range(bin_count):
			start = min_score + idx * width
			end = min_score + (idx + 1) * width if idx < bin_count - 1 else max_score
			if idx < bin_count - 1:
				bin_total = sum(1 for value in sorted_scores if start <= value < end)
			else:
				bin_total = sum(1 for value in sorted_scores if start <= value <= end)
			max_bin_count = max(max_bin_count, bin_total)
			bins.append(
				{
					"label": f"{start:.1f}-{end:.1f}",
					"count": bin_total,
				}
			)

	return {
		"count": count,
		"min": min_score,
		"max": max_score,
		"mean": avg,
		"median": med,
		"std_dev": std,
		"bins": bins,
		"max_bin_count": max_bin_count,
	}


def _generation_mode_label(prompt_version):
	version = (prompt_version or "").strip().lower()
	if version == "v1-single+review":
		return "Single-pass + refinement"
	if version == "v1-detailed":
		return "Detailed multi-pass"
	if version == "v1-detailed+review":
		return "Detailed multi-pass + refinement"
	if version == "v1-single":
		return "Single-pass"
	return "Single-pass"


def _sampling_diagnostics_labels(prompt_diagnostics):
	diagnostics = prompt_diagnostics or {}
	if not diagnostics:
		return {
			"sampling_summary": None,
			"truncation_summary": None,
		}

	files_sampled = diagnostics.get("files_sampled")
	max_files = diagnostics.get("max_files")
	total_chars = diagnostics.get("total_chars_used")
	max_total_chars = diagnostics.get("max_total_chars")
	truncated = bool(diagnostics.get("truncated"))
	truncated_file_count = diagnostics.get("truncated_file_count", 0)

	sampling_summary = (
		f"Sampled {files_sampled}/{max_files} files, {total_chars}/{max_total_chars} chars"
		if files_sampled is not None and max_files is not None and total_chars is not None and max_total_chars is not None
		else None
	)

	if truncated:
		truncation_summary = f"Truncated: yes ({truncated_file_count} file(s))"
	else:
		truncation_summary = "Truncated: no"

	return {
		"sampling_summary": sampling_summary,
		"truncation_summary": truncation_summary,
	}


def gradebook(request):
	assignments = AssignmentConfig.objects.select_related("course").prefetch_related("submissions")
	return render(request, "grading/gradebook.html", {"assignments": assignments})


def about(request):
	return render(request, "grading/about.html")


def delete_assignment(request, assignment_pk):
	if request.method != "POST":
		return redirect("grading:gradebook")

	assignment = get_object_or_404(AssignmentConfig, pk=assignment_pk)
	assignment_name = assignment.name
	assignment.delete()
	messages.success(request, f"Deleted assignment: {assignment_name}")
	return redirect("grading:gradebook")


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
			use_detailed_passes = request.POST.get("use_detailed_passes") == "1"
			use_review_pass = request.POST.get("use_review_pass") == "1"
			job, created = enqueue_batch_review_job(
				assignment,
				use_detailed_passes=use_detailed_passes,
				use_review_pass=use_review_pass,
			)
			if created:
				messages.success(
					request,
					f"Batch review started ({_batch_mode_label(use_detailed_passes, use_review_pass)}).",
				)
			else:
				messages.info(request, "A batch review job is already running for this assignment.")
		elif action == "save_instructions":
			assignment.additional_instructions = request.POST.get("additional_instructions", "").strip()
			assignment.save(update_fields=["additional_instructions"])
			messages.success(request, "Additional instructions saved.")
		elif action == "save_rubric":
			try:
				criteria_data = json.loads(request.POST.get("rubric_json", "[]"))
				assignment.rubric_criteria.all().delete()
				for idx, crit in enumerate(criteria_data):
					name = (crit.get("name") or "").strip()
					if not name:
						continue
					criterion = RubricCriterion.objects.create(
						assignment=assignment,
						name=name,
						order=idx,
					)
					for lidx, level in enumerate(crit.get("levels") or []):
						points_raw = (level.get("points") or "").strip() if isinstance(level.get("points"), str) else level.get("points")
						if points_raw is None or points_raw == "":
							continue
						RubricLevel.objects.create(
							criterion=criterion,
							points=points_raw,
							description=(level.get("description") or "").strip(),
							order=lidx,
						)
				messages.success(request, "Rubric saved.")
			except (json.JSONDecodeError, ValueError, TypeError) as exc:
				messages.error(request, f"Failed to save rubric: {exc}")
		elif action == "generate_cohort_summary":
			try:
				generate_assignment_cohort_summary(assignment)
				messages.success(request, "Cohort summary generated.")
			except Exception as exc:  # noqa: BLE001
				assignment.cohort_summary_last_error = str(exc)
				assignment.save(update_fields=["cohort_summary_last_error"])
				messages.error(request, f"Failed to generate cohort summary: {exc}")

		return redirect("grading:assignment_detail", assignment_pk=assignment.pk)

	submissions = _ordered_assignment_submissions(assignment)
	latest_job = assignment.batch_jobs.order_by("-created_at").first()
	rubric_criteria = list(assignment.rubric_criteria.prefetch_related("levels").all())
	rubric_data = [
		{
			"name": c.name,
			"levels": [
				{"points": str(level.points), "description": level.description}
				for level in c.levels.all()
			],
		}
		for c in rubric_criteria
	]
	return render(
		request,
		"grading/assignment_detail.html",
		{
			"assignment": assignment,
			"submissions": submissions,
			"latest_batch_job": latest_job,
			"latest_batch_mode": _batch_mode_label(
				latest_job.use_detailed_passes,
				latest_job.use_review_pass,
			) if latest_job else None,
			"rubric_data": rubric_data,
			"additional_instructions": assignment.additional_instructions,
			"cohort_summary_html": _sanitize_feedback_html(assignment.cohort_summary_html),
			"proposed_score_distribution": _proposed_score_distribution(submissions),
		},
	)


def assignment_batch_status(request, assignment_pk):
	assignment = get_object_or_404(AssignmentConfig, pk=assignment_pk)
	latest_job = assignment.batch_jobs.order_by("-created_at").first()
	submissions = _ordered_assignment_submissions(assignment)

	return JsonResponse(
		{
			"job": _serialize_batch_job(latest_job),
			"submissions": [_serialize_submission_row(submission) for submission in submissions],
		}
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
		model_adjustments = request.POST.get("model_adjustments", "").strip()
		if model_adjustments != (submission.model_adjustments or ""):
			submission.model_adjustments = model_adjustments
			submission.model_adjustments_last_used_at = None
			submission.save(update_fields=["model_adjustments", "model_adjustments_last_used_at"])
		try:
			if action == "generate":
				if _is_unsubmitted_record(submission):
					messages.info(request, "No submission found for this student in Canvas. Skipped draft generation.")
					return redirect("grading:submission_detail", submission_pk=submission.pk)
				use_review_pass = request.POST.get("use_review_pass") == "1"
				use_detailed_passes = request.POST.get("use_detailed_passes") == "1"
				generate_ai_draft(
					submission,
					use_review_pass=use_review_pass,
					use_detailed_passes=use_detailed_passes,
				)
				messages.success(request, "AI draft generated.")
			elif action == "save":
				submission.final_feedback = _sanitize_feedback_html(
					request.POST.get("final_feedback", submission.final_feedback)
				)
				final_score_raw = request.POST.get("final_score", "").strip()
				submission.final_score = final_score_raw or submission.final_score or submission.proposed_score
				submission.save(update_fields=["final_feedback", "final_score"])
				messages.success(request, "Review saved.")
			elif action == "approve":
				submission.final_feedback = _sanitize_feedback_html(
					request.POST.get("final_feedback", submission.proposed_feedback)
				)
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
				if _is_unsubmitted_record(submission):
					messages.info(request, "No submission found for this student in Canvas. Skipped posting.")
					return redirect("grading:submission_detail", submission_pk=submission.pk)
				submission.final_feedback = _sanitize_feedback_html(
					request.POST.get("final_feedback", submission.final_feedback or submission.proposed_feedback)
				)
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
			"editor_feedback_html": _sanitize_feedback_html(
				submission.final_feedback or submission.proposed_feedback
			),
			"proposed_score_distribution": _proposed_score_distribution(ordered_submissions),
			"ordered_submissions": ordered_submissions,
			"previous_submission_pk": previous_submission_pk,
			"next_submission_pk": next_submission_pk,
			"submission_preview": _build_submission_preview(submission),
			"artifact_summaries": [
				{
					"type": artifact.artifact_type,
					"name": _artifact_display_name(artifact),
				}
				for artifact in submission.artifacts.all()
			],
			"draft_history": [
				{
					"created_at": draft.created_at,
					"provider_name": draft.provider_name,
					"model_name": draft.model_name,
					"generation_mode": _generation_mode_label(draft.prompt_version),
					"sampling_summary": _sampling_diagnostics_labels(draft.prompt_diagnostics).get("sampling_summary"),
					"truncation_summary": _sampling_diagnostics_labels(draft.prompt_diagnostics).get("truncation_summary"),
					"draft_score": draft.draft_score,
					"feedback_html": _sanitize_feedback_html(draft.draft_feedback),
				}
				for draft in submission.ai_drafts.all().order_by("-created_at")
			],
		},
	)

