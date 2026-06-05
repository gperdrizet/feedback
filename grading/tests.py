import json
import tempfile
from pathlib import Path
from decimal import Decimal
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from grading.models import AssignmentConfig, BatchReviewJob, CourseSync, SubmissionArtifact, SubmissionRecord


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

	@patch("grading.views.threading.Thread.start")
	def test_assignment_batch_review_starts_async_job(self, mock_thread_start):
		response = self.client.post(
			reverse("grading:assignment_detail", args=[self.assignment.pk]),
			{"action": "batch_review"},
		)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers["Location"], reverse("grading:assignment_detail", args=[self.assignment.pk]))
		self.assertEqual(BatchReviewJob.objects.filter(assignment=self.assignment).count(), 1)
		job = BatchReviewJob.objects.get(assignment=self.assignment)
		self.assertEqual(job.status, BatchReviewJob.Status.QUEUED)
		mock_thread_start.assert_called_once()

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

	@patch("grading.views.requests.get")
	def test_submission_review_renders_linked_notebook(self, mock_get):
		mock_response = Mock()
		mock_response.raise_for_status.return_value = None
		mock_response.json.return_value = {
			"cells": [
				{"cell_type": "markdown", "metadata": {}, "source": ["Linked notebook\n"]},
			]
		}
		mock_get.return_value = mock_response

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
		mock_get.assert_called_once_with("https://example.com/student-notebook.ipynb", timeout=30)

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
