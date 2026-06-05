from django.db import models
from django.utils import timezone


class CourseSync(models.Model):
    canvas_course_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=255)
    course_code = models.CharField(max_length=100, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.canvas_course_id})"


class AssignmentConfig(models.Model):
    class WorkflowState(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    course = models.ForeignKey(CourseSync, on_delete=models.CASCADE, related_name="assignments")
    canvas_assignment_id = models.BigIntegerField()
    name = models.CharField(max_length=255)
    assignment_description = models.TextField(blank=True)
    points_possible = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    require_manual_approval = models.BooleanField(default=True)
    workflow_state = models.CharField(
        max_length=20,
        choices=WorkflowState.choices,
        default=WorkflowState.ACTIVE,
    )
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("course", "canvas_assignment_id")

    def __str__(self):
        return f"{self.name} ({self.canvas_assignment_id})"


class SubmissionRecord(models.Model):
    class AIStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not Started"
        PROCESSING = "processing", "Processing"
        COMPLETE = "complete", "Complete"
        ERROR = "error", "Error"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        POSTED = "posted", "Posted"

    assignment = models.ForeignKey(AssignmentConfig, on_delete=models.CASCADE, related_name="submissions")
    canvas_submission_id = models.BigIntegerField()
    canvas_user_id = models.BigIntegerField()
    student_name = models.CharField(max_length=255)
    submission_type = models.CharField(max_length=50, blank=True)
    submission_url = models.URLField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    canvas_workflow_state = models.CharField(max_length=64, blank=True)
    ai_status = models.CharField(max_length=20, choices=AIStatus.choices, default=AIStatus.NOT_STARTED)
    review_status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    proposed_score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    final_score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    proposed_feedback = models.TextField(blank=True)
    final_feedback = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("assignment", "canvas_submission_id")

    def __str__(self):
        return f"{self.student_name} / {self.assignment.name}"


class SubmissionArtifact(models.Model):
    class ArtifactType(models.TextChoices):
        ATTACHMENT = "attachment", "Attachment"
        ONLINE_URL = "online_url", "Online URL"

    submission = models.ForeignKey(SubmissionRecord, on_delete=models.CASCADE, related_name="artifacts")
    artifact_type = models.CharField(max_length=20, choices=ArtifactType.choices)
    source_url = models.URLField(blank=True)
    local_path = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class AIFeedbackDraft(models.Model):
    submission = models.ForeignKey(SubmissionRecord, on_delete=models.CASCADE, related_name="ai_drafts")
    provider_name = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=50, default="v1")
    draft_feedback = models.TextField()
    draft_score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class ApprovalDecision(models.Model):
    class DecisionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    submission = models.ForeignKey(SubmissionRecord, on_delete=models.CASCADE, related_name="approval_decisions")
    status = models.CharField(max_length=20, choices=DecisionStatus.choices, default=DecisionStatus.PENDING)
    instructor_username = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class CanvasPostAttempt(models.Model):
    submission = models.ForeignKey(SubmissionRecord, on_delete=models.CASCADE, related_name="post_attempts")
    attempted_at = models.DateTimeField(default=timezone.now)
    success = models.BooleanField(default=False)
    response_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)


class BatchReviewJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    assignment = models.ForeignKey(AssignmentConfig, on_delete=models.CASCADE, related_name="batch_jobs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    total_submissions = models.PositiveIntegerField(default=0)
    completed_submissions = models.PositiveIntegerField(default=0)
    failed_submissions = models.PositiveIntegerField(default=0)
    current_student_name = models.CharField(max_length=255, blank=True)
    summary_message = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
