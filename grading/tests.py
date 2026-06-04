from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from grading.models import AssignmentConfig, CourseSync, SubmissionRecord


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

	@patch("grading.views.generate_ai_draft")
	@patch("grading.views.sync_assignment_from_canvas")
	def test_assignment_batch_review_triggers_sync_and_drafts(self, mock_sync, mock_generate):
		response = self.client.post(
			reverse("grading:assignment_detail", args=[self.assignment.pk]),
			{"action": "batch_review"},
		)

		self.assertEqual(response.status_code, 302)
		mock_sync.assert_called_once_with(
			course_id=self.course.canvas_course_id,
			assignment_id=self.assignment.canvas_assignment_id,
			download_root="submissions",
		)
		self.assertEqual(mock_generate.call_count, 3)
		self.assertEqual(response.headers["Location"], reverse("grading:submission_detail", args=[self.first_submission.pk]))

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
