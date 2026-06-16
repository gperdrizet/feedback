import json
import tempfile
from pathlib import Path
from decimal import Decimal
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from grading.models import AIFeedbackDraft, AssignmentConfig, BatchReviewJob, CourseSync, SubmissionArtifact, SubmissionRecord
from grading.services.ai_provider import OpenAICompatibleProvider
from grading.services.canvas_sync import generate_ai_draft, post_submission_to_canvas
from grading.services.batch_jobs import run_batch_review_job


class ReviewWorkflowTests(TestCase):
	def setUp(self):
		self.course = CourseSync.objects.create(canvas_course_id=101, name="Course 101")
		self.assignment = AssignmentConfig.objects.create(
			course=self.course,
			canvas_assignment_id=202,
			name="Essay 1",
			points_possible=100,
		)
		self.first_submission = SubmissionRecord.objects.create(
			assignment=self.assignment,
			canvas_submission_id=1,
			canvas_user_id=11,
			student_name="Alice",
			proposed_score=Decimal("91.00"),
			proposed_feedback="Good work.",
		)
		self.second_submission = SubmissionRecord.objects.create(
			assignment=self.assignment,
			canvas_submission_id=2,
			canvas_user_id=12,
			student_name="Bob",
			proposed_score=Decimal("82.00"),
			proposed_feedback="Solid draft.",
		)
		self.third_submission = SubmissionRecord.objects.create(
			assignment=self.assignment,
			canvas_submission_id=3,
			canvas_user_id=13,
			student_name="Charlie",
			proposed_score=Decimal("76.00"),
			proposed_feedback="Needs revision.",
		)
		self.tempdir = tempfile.TemporaryDirectory()
		self.addCleanup(self.tempdir.cleanup)

	def _write_notebook(self, filename, cells):
		path = Path(self.tempdir.name) / filename
		path.write_text(json.dumps({"cells": cells}), encoding="utf-8")
		return path

	def test_assignment_batch_review_starts_async_job(self):
		response = self.client.post(
			reverse("grading:assignment_detail", args=[self.assignment.pk]),
			{"action": "batch_review"},
		)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers["Location"], reverse("grading:assignment_detail", args=[self.assignment.pk]))
		self.assertEqual(BatchReviewJob.objects.filter(assignment=self.assignment).count(), 1)
		job = BatchReviewJob.objects.get(assignment=self.assignment)
		self.assertEqual(job.status, BatchReviewJob.Status.QUEUED)
		self.assertFalse(job.use_detailed_passes)
		self.assertFalse(job.use_review_pass)

	def test_assignment_batch_review_can_set_generation_modes(self):
		response = self.client.post(
			reverse("grading:assignment_detail", args=[self.assignment.pk]),
			{
				"action": "batch_review",
				"use_detailed_passes": "1",
				"use_review_pass": "1",
			},
		)

		self.assertEqual(response.status_code, 302)
		job = BatchReviewJob.objects.get(assignment=self.assignment)
		self.assertTrue(job.use_detailed_passes)
		self.assertTrue(job.use_review_pass)

	@patch("grading.services.batch_jobs.generate_ai_draft")
	@patch("grading.services.batch_jobs.sync_assignment")
	def test_batch_worker_uses_job_generation_modes(self, mock_sync_assignment, mock_generate_ai_draft):
		job = BatchReviewJob.objects.create(
			assignment=self.assignment,
			status=BatchReviewJob.Status.QUEUED,
			use_detailed_passes=True,
			use_review_pass=True,
		)

		run_batch_review_job(job.pk)

		self.assertEqual(mock_generate_ai_draft.call_count, 3)
		for call in mock_generate_ai_draft.call_args_list:
			self.assertEqual(call.kwargs["use_detailed_passes"], True)
			self.assertEqual(call.kwargs["use_review_pass"], True)

	def test_assignment_batch_status_endpoint_returns_job_and_rows(self):
		job = BatchReviewJob.objects.create(
			assignment=self.assignment,
			status=BatchReviewJob.Status.RUNNING,
			total_submissions=3,
			completed_submissions=1,
			failed_submissions=0,
			current_student_name="Bob",
		)

		response = self.client.get(reverse("grading:assignment_batch_status", args=[self.assignment.pk]))

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(payload["job"]["id"], job.pk)
		self.assertEqual(payload["job"]["status"], BatchReviewJob.Status.RUNNING)
		self.assertEqual(payload["job"]["current_student_name"], "Bob")
		self.assertEqual(len(payload["submissions"]), 3)

	def test_assignment_detail_shows_proposed_score_distribution(self):
		response = self.client.get(reverse("grading:assignment_detail", args=[self.assignment.pk]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Proposed Score Distribution")
		self.assertContains(response, "Mean")
		self.assertContains(response, "Median")

	def test_about_page_renders_workflow_sections(self):
		response = self.client.get(reverse("grading:about"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Workflow Diagram")
		self.assertContains(response, "Evidence Pass")
		self.assertContains(response, "Consistency Pass")

	@patch("grading.views.enqueue_cohort_summary_job")
	def test_assignment_cohort_summary_generation_enqueues_job(self, mock_enqueue):
		self.assignment.cohort_summary_html = "<p>Cohort quality is strong overall.</p>"
		self.assignment.save(update_fields=["cohort_summary_html"])
		mock_enqueue.return_value = (Mock(), True)

		response = self.client.post(
			reverse("grading:assignment_detail", args=[self.assignment.pk]),
			{"action": "generate_cohort_summary"},
		)

		self.assertEqual(response.status_code, 302)
		mock_enqueue.assert_called_once()
		follow = self.client.get(reverse("grading:assignment_detail", args=[self.assignment.pk]))
		self.assertContains(follow, "Cohort Summary")
		self.assertContains(follow, "Cohort quality is strong overall")

	@patch("grading.views.enqueue_cohort_summary_job")
	def test_assignment_cohort_summary_generation_duplicate_job_is_reported(self, mock_enqueue):
		mock_enqueue.return_value = (Mock(), False)

		response = self.client.post(
			reverse("grading:assignment_detail", args=[self.assignment.pk]),
			{"action": "generate_cohort_summary"},
		)

		self.assertEqual(response.status_code, 302)
		mock_enqueue.assert_called_once()

	def test_assignment_cohort_summary_status_endpoint_returns_job_status(self):
		from grading.models import CohortSummaryJob
		job = CohortSummaryJob.objects.create(
			assignment=self.assignment,
			status=CohortSummaryJob.Status.RUNNING,
			summary_message="Generating...",
		)

		response = self.client.get(
			reverse("grading:assignment_cohort_summary_status", args=[self.assignment.pk]),
			headers={"Accept": "application/json"},
		)

		self.assertEqual(response.status_code, 200)
		data = response.json()
		self.assertIn("job", data)
		self.assertEqual(data["job"]["status"], "running")
		self.assertEqual(data["job"]["summary_message"], "Generating...")

	def test_assignment_views_use_latest_submission_per_student(self):
		SubmissionRecord.objects.create(
			assignment=self.assignment,
			canvas_submission_id=99,
			canvas_user_id=self.first_submission.canvas_user_id,
			student_name=self.first_submission.student_name,
			submitted_at=timezone.now() + timedelta(minutes=1),
			proposed_score=Decimal("97.00"),
		)

		status_response = self.client.get(reverse("grading:assignment_batch_status", args=[self.assignment.pk]))
		status_payload = status_response.json()

		self.assertEqual(status_response.status_code, 200)
		self.assertEqual(len(status_payload["submissions"]), 3)

		assignment_response = self.client.get(reverse("grading:assignment_detail", args=[self.assignment.pk]))
		self.assertEqual(assignment_response.status_code, 200)
		rendered_ids = [submission.canvas_submission_id for submission in assignment_response.context["submissions"]]
		self.assertIn(99, rendered_ids)
		self.assertNotIn(self.first_submission.canvas_submission_id, rendered_ids)

	@patch("grading.services.canvas_sync._get_canvas_client")
	def test_list_assignment_submissions_skips_unsubmitted(self, mock_get_canvas_client):
		from grading.services.canvas_sync import list_assignment_submissions

		submitted = SimpleNamespace(
			id=1001,
			user_id=11,
			submission_type="online_text_entry",
			workflow_state="submitted",
			submitted_at="2026-06-01T10:00:00Z",
			url="",
			user={"id": 11, "name": "Alice"},
			attachments=[],
		)
		unsubmitted = SimpleNamespace(
			id=1002,
			user_id=12,
			submission_type="online_text_entry",
			workflow_state="unsubmitted",
			submitted_at=None,
			url="",
			user={"id": 12, "name": "Bob"},
			attachments=[],
		)

		assignment = Mock()
		assignment.get_submissions.return_value = [submitted, unsubmitted]
		course = Mock()
		course.get_assignment.return_value = assignment
		canvas = Mock()
		canvas.get_course.return_value = course
		mock_get_canvas_client.return_value = canvas

		payloads = list_assignment_submissions(course_id=101, assignment_id=202)

		self.assertEqual(len(payloads), 1)
		self.assertEqual(payloads[0]["user_id"], 11)

	def test_delete_assignment_removes_record(self):
		response = self.client.post(reverse("grading:delete_assignment", args=[self.assignment.pk]))

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers["Location"], reverse("grading:gradebook"))
		self.assertFalse(AssignmentConfig.objects.filter(pk=self.assignment.pk).exists())

	def test_submission_review_shows_navigation_links(self):
		response = self.client.get(reverse("grading:submission_detail", args=[self.second_submission.pk]))

		self.assertContains(response, "Previous", status_code=200)
		self.assertContains(response, "Next", status_code=200)
		self.assertContains(response, self.first_submission.student_name)
		self.assertContains(response, self.second_submission.student_name)
		self.assertContains(response, self.third_submission.student_name)

	def test_submission_review_save_updates_score_and_feedback(self):
		response = self.client.post(
			reverse("grading:submission_detail", args=[self.first_submission.pk]),
			{
				"action": "save",
				"final_score": "88",
				"final_feedback": "Revised feedback.",
			},
		)

		self.assertEqual(response.status_code, 302)
		self.first_submission.refresh_from_db()
		self.assertEqual(self.first_submission.final_score, Decimal("88"))
		self.assertEqual(self.first_submission.final_feedback, "Revised feedback.")

	@patch("grading.views.post_submission_to_canvas")
	def test_submission_review_post_calls_canvas_post(self, mock_post):
		response = self.client.post(
			reverse("grading:submission_detail", args=[self.first_submission.pk]),
			{
				"action": "post",
				"final_score": "87",
				"final_feedback": "Ready to post.",
			},
		)

		self.assertEqual(response.status_code, 302)
		mock_post.assert_called_once()
		self.first_submission.refresh_from_db()
		self.assertEqual(self.first_submission.final_score, Decimal("87"))
		self.assertEqual(self.first_submission.final_feedback, "Ready to post.")

	@patch("grading.views.generate_ai_draft")
	def test_submission_generate_persists_model_adjustments(self, mock_generate):
		response = self.client.post(
			reverse("grading:submission_detail", args=[self.first_submission.pk]),
			{
				"action": "generate",
				"model_adjustments": "Please be stricter on statistical interpretation and cite exact lines.",
			},
		)

		self.assertEqual(response.status_code, 302)
		mock_generate.assert_called_once()
		self.first_submission.refresh_from_db()
		self.assertEqual(
			self.first_submission.model_adjustments,
			"Please be stricter on statistical interpretation and cite exact lines.",
		)
		mock_generate.assert_called_with(
			self.first_submission,
			use_review_pass=False,
			use_detailed_passes=False,
		)

	@patch("grading.views.generate_ai_draft")
	def test_submission_generate_can_enable_second_pass(self, mock_generate):
		response = self.client.post(
			reverse("grading:submission_detail", args=[self.first_submission.pk]),
			{
				"action": "generate",
				"use_review_pass": "1",
			},
		)

		self.assertEqual(response.status_code, 302)
		mock_generate.assert_called_once_with(
			self.first_submission,
			use_review_pass=True,
			use_detailed_passes=False,
		)

	@patch("grading.views.generate_ai_draft")
	def test_submission_generate_can_enable_detailed_passes(self, mock_generate):
		response = self.client.post(
			reverse("grading:submission_detail", args=[self.first_submission.pk]),
			{
				"action": "generate",
				"use_detailed_passes": "1",
			},
		)

		self.assertEqual(response.status_code, 302)
		mock_generate.assert_called_once_with(
			self.first_submission,
			use_review_pass=False,
			use_detailed_passes=True,
		)

	@patch("grading.views.generate_ai_draft")
	def test_submission_generate_skips_unsubmitted_record(self, mock_generate):
		self.first_submission.canvas_workflow_state = "unsubmitted"
		self.first_submission.save(update_fields=["canvas_workflow_state"])

		response = self.client.post(
			reverse("grading:submission_detail", args=[self.first_submission.pk]),
			{"action": "generate"},
		)

		self.assertEqual(response.status_code, 302)
		mock_generate.assert_not_called()

	@patch("grading.services.canvas_sync.post_submission_grade")
	def test_post_to_canvas_sends_html_comment(self, mock_post_grade):
		mock_post_grade.return_value = {"ok": True}
		self.first_submission.review_status = SubmissionRecord.ReviewStatus.APPROVED
		self.first_submission.final_score = Decimal("91.00")
		self.first_submission.final_feedback = (
			"<p><strong>Summary</strong></p>"
			"<ul><li>Great work on feature engineering.</li></ul>"
			"<p>See <a href='https://example.com'>notes</a>.</p>"
		)
		self.first_submission.save(update_fields=["review_status", "final_score", "final_feedback"])

		post_submission_to_canvas(self.first_submission)

		self.assertTrue(mock_post_grade.called)
		kwargs = mock_post_grade.call_args.kwargs
		self.assertEqual(kwargs["posted_grade"], "91.00")
		self.assertEqual(kwargs["comment_format"], "html")
		self.assertEqual(kwargs["comment"], self.first_submission.final_feedback)

	@patch("grading.services.canvas_sync.post_submission_grade")
	def test_post_to_canvas_coerces_non_json_response_payload(self, mock_post_grade):
		mock_post_grade.return_value = {"canvas_response": object(), "ok": True}
		self.first_submission.review_status = SubmissionRecord.ReviewStatus.APPROVED
		self.first_submission.final_score = Decimal("92.00")
		self.first_submission.final_feedback = "<p>Ready to post</p>"
		self.first_submission.save(update_fields=["review_status", "final_score", "final_feedback"])

		response = post_submission_to_canvas(self.first_submission)

		self.assertEqual(response["ok"], True)
		self.assertIsInstance(response["canvas_response"], str)

	@patch("grading.views.post_submission_to_canvas")
	def test_submission_post_skips_unsubmitted_record(self, mock_post):
		self.first_submission.canvas_workflow_state = "unsubmitted"
		self.first_submission.save(update_fields=["canvas_workflow_state"])

		response = self.client.post(
			reverse("grading:submission_detail", args=[self.first_submission.pk]),
			{
				"action": "post",
				"final_score": "87",
				"final_feedback": "Should not post",
			},
		)

		self.assertEqual(response.status_code, 302)
		mock_post.assert_not_called()

	@patch("grading.services.canvas_sync.post_submission_grade")
	def test_post_to_canvas_skips_unsubmitted_record(self, mock_post_grade):
		self.first_submission.canvas_workflow_state = "unsubmitted"
		self.first_submission.review_status = SubmissionRecord.ReviewStatus.APPROVED
		self.first_submission.final_score = Decimal("87.00")
		self.first_submission.save(update_fields=["canvas_workflow_state", "review_status", "final_score"])

		response = post_submission_to_canvas(self.first_submission)

		self.assertEqual(response["success"], True)
		self.assertEqual(response["skipped"], True)
		mock_post_grade.assert_not_called()

	def test_submission_review_renders_local_notebook_artifact(self):
		notebook_path = self._write_notebook(
			"submission.ipynb",
			[
				{"cell_type": "markdown", "metadata": {}, "source": ["# Notebook Title\n", "Hello **world**\n"]},
				{"cell_type": "code", "metadata": {"language": "python"}, "source": ["print('hi')\n"]},
			],
		)
		notebook_submission = SubmissionRecord.objects.create(
			assignment=self.assignment,
			canvas_submission_id=4,
			canvas_user_id=14,
			student_name="Dana",
			proposed_score=Decimal("95.00"),
			proposed_feedback="Looks good.",
		)
		SubmissionArtifact.objects.create(
			submission=notebook_submission,
			artifact_type=SubmissionArtifact.ArtifactType.ATTACHMENT,
			local_path=str(notebook_path),
		)

		response = self.client.get(reverse("grading:submission_detail", args=[notebook_submission.pk]))

		self.assertContains(response, "Submission")
		self.assertContains(response, "Notebook Title")
		self.assertContains(response, "Hello")
		self.assertContains(response, "print")
		self.assertContains(response, "highlight")

	@patch("grading.views.fetch_remote_text")
	def test_submission_review_renders_linked_notebook(self, mock_fetch):
		mock_fetch.return_value = json.dumps({
			"cells": [
				{"cell_type": "markdown", "metadata": {}, "source": ["Linked notebook\n"]},
			]
		})

		linked_submission = SubmissionRecord.objects.create(
			assignment=self.assignment,
			canvas_submission_id=5,
			canvas_user_id=15,
			student_name="Eli",
			proposed_score=Decimal("88.00"),
			proposed_feedback="Fine.",
			submission_url="https://example.com/student-notebook.ipynb",
		)

		response = self.client.get(reverse("grading:submission_detail", args=[linked_submission.pk]))

		self.assertContains(response, "Submission")
		self.assertContains(response, "Linked notebook")
		mock_fetch.assert_called_once()

	def test_submission_review_renders_local_python_artifact(self):
		script_path = Path(self.tempdir.name) / "main.py"
		script_path.write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")

		script_submission = SubmissionRecord.objects.create(
			assignment=self.assignment,
			canvas_submission_id=6,
			canvas_user_id=16,
			student_name="Fern",
			proposed_score=Decimal("90.00"),
			proposed_feedback="Good script.",
		)
		SubmissionArtifact.objects.create(
			submission=script_submission,
			artifact_type=SubmissionArtifact.ArtifactType.ATTACHMENT,
			local_path=str(script_path),
		)

		response = self.client.get(reverse("grading:submission_detail", args=[script_submission.pk]))

		self.assertContains(response, "Submission")
		self.assertContains(response, "Python cell")
		self.assertContains(response, "greet")
		self.assertContains(response, "highlight")

	def test_submission_review_displays_previous_drafts(self):
		AIFeedbackDraft.objects.create(
			submission=self.first_submission,
			provider_name="provider-a",
			model_name="model-a",
			prompt_version="v1-detailed",
			prompt_diagnostics={
				"truncated": True,
				"truncated_file_count": 1,
				"files_sampled": 2,
				"max_files": 8,
				"total_chars_used": 40000,
				"max_total_chars": 52000,
			},
			draft_feedback="<p>First draft body</p>",
			draft_score=Decimal("85.00"),
		)
		AIFeedbackDraft.objects.create(
			submission=self.first_submission,
			provider_name="provider-b",
			model_name="model-b",
			prompt_version="v1-single+review",
			prompt_diagnostics={
				"truncated": False,
				"truncated_file_count": 0,
				"files_sampled": 1,
				"max_files": 8,
				"total_chars_used": 12000,
				"max_total_chars": 52000,
			},
			draft_feedback="<p>Second draft body</p>",
			draft_score=Decimal("88.00"),
		)

		response = self.client.get(reverse("grading:submission_detail", args=[self.first_submission.pk]))

		self.assertContains(response, "Previous Drafts")
		self.assertContains(response, "First draft body")
		self.assertContains(response, "Second draft body")
		self.assertContains(response, "Provider: provider-a")
		self.assertContains(response, "Provider: provider-b")
		self.assertContains(response, "Mode: Detailed multi-pass")
		self.assertContains(response, "Mode: Single-pass + refinement")
		self.assertContains(response, "1 file(s) truncated")
		self.assertContains(response, "No files truncated")
		self.assertContains(response, "Included 2 of 8 max files (40,000 of 52,000 chars)")

	def test_submission_editor_falls_back_to_latest_draft_feedback(self):
		self.first_submission.final_feedback = ""
		self.first_submission.proposed_feedback = ""
		self.first_submission.save(update_fields=["final_feedback", "proposed_feedback"])

		AIFeedbackDraft.objects.create(
			submission=self.first_submission,
			provider_name="provider-fallback",
			model_name="model-fallback",
			prompt_version="v1-single",
			draft_feedback="<p>Fallback draft content</p>",
		)

		response = self.client.get(reverse("grading:submission_detail", args=[self.first_submission.pk]))

		self.assertContains(response, "Fallback draft content")
		self.assertIn("Fallback draft content", response.context["editor_feedback_html"])

	@patch("grading.services.canvas_sync.OpenAICompatibleProvider")
	def test_generate_ai_draft_records_generation_mode_in_prompt_version(self, mock_provider_cls):
		provider = mock_provider_cls.return_value
		provider.generate_feedback.return_value = SimpleNamespace(
			feedback="<p>Generated feedback</p>",
			score=91,
			provider_name="test-provider",
			model_name="test-model",
			prompt_diagnostics={"truncated": True, "truncated_file_count": 1},
		)

		generate_ai_draft(
			self.first_submission,
			use_detailed_passes=True,
			use_review_pass=True,
		)

		draft = self.first_submission.ai_drafts.order_by("-created_at").first()
		self.assertIsNotNone(draft)
		self.assertEqual(draft.prompt_version, "v1-detailed+review")
		self.assertEqual(draft.prompt_diagnostics.get("truncated"), True)

	def test_ai_provider_extracts_notebook_cells_for_prompt(self):
		notebook_path = self._write_notebook(
			"prompt_sample.ipynb",
			[
				{"cell_type": "markdown", "metadata": {}, "source": ["# Header\n", "Some notes\n"]},
				{"cell_type": "code", "metadata": {}, "source": ["print('hello notebook')\n"]},
			],
		)
		provider = object.__new__(OpenAICompatibleProvider)
		samples = provider._read_text_samples([SimpleNamespace(local_path=str(notebook_path))])

		self.assertIn("Header", samples)
		self.assertIn("hello notebook", samples)

	def test_ai_provider_includes_late_notebook_content_in_prompt_sample(self):
		long_markdown = "A" * 3500
		notebook_path = self._write_notebook(
			"long_prompt_sample.ipynb",
			[
				{"cell_type": "markdown", "metadata": {}, "source": [long_markdown]},
				{"cell_type": "code", "metadata": {}, "source": ["print('LATE_NOTEBOOK_MARKER')\n"]},
			],
		)
		provider = object.__new__(OpenAICompatibleProvider)
		samples = provider._read_text_samples([SimpleNamespace(local_path=str(notebook_path))])

		self.assertIn("LATE_NOTEBOOK_MARKER", samples)

	def test_ai_provider_includes_late_python_content_in_prompt_sample(self):
		script_path = Path(self.tempdir.name) / "long_main.py"
		script_path.write_text(("# filler\n" * 700) + "print('LATE_PYTHON_MARKER')\n", encoding="utf-8")
		provider = object.__new__(OpenAICompatibleProvider)
		samples = provider._read_text_samples([SimpleNamespace(local_path=str(script_path))])

		self.assertIn("LATE_PYTHON_MARKER", samples)

	def test_ai_provider_preserves_file_tail_when_truncated(self):
		script_path = Path(self.tempdir.name) / "tail_main.py"
		script_path.write_text(("x = 1\n" * 5000) + "main_menu()\n", encoding="utf-8")
		provider = object.__new__(OpenAICompatibleProvider)
		samples = provider._read_text_samples(
			[SimpleNamespace(local_path=str(script_path))],
			max_chars_per_file=4000,
			max_total_chars=4000,
		)

		self.assertIn("main_menu()", samples)

	def test_ai_provider_preserves_notebook_tail_when_truncated(self):
		notebook_path = self._write_notebook(
			"tail_prompt_sample.ipynb",
			[
				{"cell_type": "markdown", "metadata": {}, "source": ["A" * 20000]},
				{"cell_type": "code", "metadata": {}, "source": ["main_menu()\n"]},
			],
		)
		provider = object.__new__(OpenAICompatibleProvider)
		samples = provider._read_text_samples(
			[SimpleNamespace(local_path=str(notebook_path))],
			max_chars_per_file=5000,
			max_total_chars=5000,
		)

		self.assertIn("main_menu()", samples)

	def test_ai_provider_extracts_score_from_total_row(self):
		provider = object.__new__(OpenAICompatibleProvider)
		feedback = (
			"<p>Score breakdown:</p>"
			"<table><tbody>"
			"<tr><td>Criterion A</td><td>20</td></tr>"
			"<tr><td>Total</td><td>88.50</td></tr>"
			"</tbody></table>"
		)

		score = provider._extract_score_from_feedback(feedback)

		self.assertEqual(score, 88.50)

	def test_ai_provider_extract_score_returns_none_when_missing(self):
		provider = object.__new__(OpenAICompatibleProvider)
		feedback = "<p>Great work overall. Keep practicing loops and functions.</p>"

		score = provider._extract_score_from_feedback(feedback)

		self.assertIsNone(score)
