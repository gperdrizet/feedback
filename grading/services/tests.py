"""Service-level tests for grading workflow."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from grading.models import AssignmentConfig, CourseSync, SubmissionRecord
from grading.services.ai_provider import AIDraftResult
from grading.services.canvas_sync import approve_submission, generate_ai_draft, post_submission_to_canvas


class CanvasWorkflowServiceTests(TestCase):
    def setUp(self):
        self.course = CourseSync.objects.create(canvas_course_id=10, name="Course 10")
        self.assignment = AssignmentConfig.objects.create(
            course=self.course,
            canvas_assignment_id=20,
            name="Assignment 20",
            assignment_description="Grade on readability and tests.",
        )
        self.submission = SubmissionRecord.objects.create(
            assignment=self.assignment,
            canvas_submission_id=30,
            canvas_user_id=40,
            student_name="Student One",
        )

    @patch("grading.services.canvas_sync.OpenAICompatibleProvider")
    def test_generate_ai_draft_transitions_to_complete(self, mock_provider_cls):
        mock_provider = mock_provider_cls.return_value
        mock_provider.generate_feedback.return_value = AIDraftResult(
            feedback="Draft feedback",
            score=None,
            provider_name="test-provider",
            model_name="test-model",
        )

        generate_ai_draft(self.submission)
        self.submission.refresh_from_db()

        self.assertEqual(self.submission.ai_status, SubmissionRecord.AIStatus.COMPLETE)
        self.assertTrue(self.submission.proposed_feedback)
        self.assertEqual(self.submission.ai_drafts.count(), 1)

    def test_post_requires_approval(self):
        self.submission.final_score = Decimal("95")
        self.submission.save(update_fields=["final_score"])

        with self.assertRaises(ValueError):
            post_submission_to_canvas(self.submission)

    @patch("grading.services.canvas_sync.post_submission_grade")
    def test_post_after_approval(self, mock_post_submission_grade):
        mock_post_submission_grade.return_value = {"success": True}

        self.submission.proposed_score = Decimal("92")
        self.submission.proposed_feedback = "Good structure."
        self.submission.save(update_fields=["proposed_score", "proposed_feedback"])

        approve_submission(self.submission, instructor_username="instructor")
        post_submission_to_canvas(self.submission)

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.review_status, SubmissionRecord.ReviewStatus.POSTED)
        self.assertEqual(self.submission.post_attempts.count(), 1)
