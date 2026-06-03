from django.contrib import admin

from grading.models import (
	AIFeedbackDraft,
	ApprovalDecision,
	AssignmentConfig,
	CanvasPostAttempt,
	CourseSync,
	SubmissionArtifact,
	SubmissionRecord,
)

admin.site.register(CourseSync)
admin.site.register(AssignmentConfig)
admin.site.register(SubmissionRecord)
admin.site.register(SubmissionArtifact)
admin.site.register(AIFeedbackDraft)
admin.site.register(ApprovalDecision)
admin.site.register(CanvasPostAttempt)
