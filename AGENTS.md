# Agent Orientation: Feedback Grading System

This document helps AI coding agents quickly understand the feedback system architecture, implementation patterns, and development workflow.

## Project Overview

**Purpose**: Django web application for AI-assisted grading of Canvas LMS assignments with instructor approval workflow.

**Tech Stack**: Django 3.x, PostgreSQL, OpenAI-compatible AI providers (Promptly), canvas-instructor-tools package, Docker deployment.

**Core Pattern**: Download submissions → Generate AI drafts → Instructor review/edit → Post to Canvas.

## Architecture

### Django Apps

- **`grading/`**: Main application containing models, views, services, templates, tests
- **`feedback_project/`**: Django project settings and WSGI configuration

### Key Models (`grading/models.py`)

- **`CourseSync`**: Canvas course record (course_id, name)
- **`AssignmentConfig`**: Canvas assignment with rubric, instructions, cohort summary fields
- **`SubmissionRecord`**: Student submission with AI/review status, scores, feedback text
- **`SubmissionArtifact`**: Downloaded files/URLs from submission
- **`AIFeedbackDraft`**: Historical AI-generated drafts with diagnostics
- **`PostAttempt`**: Audit log of Canvas posting attempts
- **`BatchReviewJob`**: Background job for bulk draft generation
- **`CohortSummaryJob`**: Background job for assignment-level summary generation
- **`RubricCriterion` / `RubricLevel`**: Scoring rubric definitions

### Service Layer (`grading/services/`)

- **`canvas_sync.py`**: Canvas API integration, workflow orchestration
  - `sync_assignment()`: Download submissions and artifacts
  - `generate_ai_draft()`: Generate feedback, persist diagnostics
  - `post_submission_to_canvas()`: Upload grade/comment
  - `approve_submission()`: Transition to APPROVED state
  - `_is_unsubmitted_record()`: Skip empty submissions

- **`ai_provider.py`**: OpenAI-compatible provider integration
  - `OpenAICompatibleProvider`: Main provider class
  - `_head_tail_sample()`: 70/30 file truncation (preserves start and end)
  - `_notebook_text_sample()`: Extract text from Jupyter notebooks
  - `_read_text_samples()`: Build prompt with diagnostics tracking
  - `generate_feedback()`: Main feedback generation endpoint
  - `generate_cohort_summary()`: Assignment-level analysis

- **`batch_jobs.py`**: Background batch processing
  - `enqueue_batch_review_job()`: Create job record
  - `claim_next_queued_job()`: Worker queue mechanism
  - `run_batch_review_job()`: Execute batch generation

- **`cohort_summary_jobs.py`**: Async cohort summary generation
  - `enqueue_cohort_summary_job()`: Create job record
  - `claim_next_queued_cohort_summary_job()`: Worker queue
  - `run_cohort_summary_job()`: Execute summary generation

### Views (`grading/views.py`)

- **`gradebook`**: Main assignment list
- **`assignment_detail`**: Assignment overview with batch controls, cohort summary, rubric editor
- **`submission_detail`**: Individual submission review UI with draft history
- **`assignment_batch_status`**: JSON polling endpoint for batch progress
- **`assignment_cohort_summary_status`**: JSON polling endpoint for cohort job
- **`sync_assignment`**: Canvas sync form submission handler

### Templates (`grading/templates/grading/`)

- **`gradebook.html`**: Assignment list view
- **`assignment_detail.html`**: Batch controls, cohort summary panel, rubric editor, submissions table
- **`submission_detail.html`**: Editor, approval/post buttons, draft history with diagnostics
- JavaScript polling for live batch/cohort status updates

## Key Features & Implementation

### 1. Unsubmitted Student Handling

**Pattern**: Check `canvas_workflow_state == "unsubmitted"` and skip processing.

**Locations**:
- `canvas_sync._is_unsubmitted_record()`: Detection helper
- `canvas_sync.generate_ai_draft()`: Returns `False` for unsubmitted
- `canvas_sync.post_submission_to_canvas()`: Returns skip dict
- `batch_jobs._ordered_assignment_submissions()`: Filters from batch queue
- `views.submission_detail`: Shows info message for skipped actions

**Rationale**: Canvas creates submission records even when students don't submit; avoid posting "you didn't submit" feedback.

### 2. Head+Tail Truncation

**Pattern**: When files exceed char limits, keep first 70% and last 30% with marker: `[... truncated middle content ...]`

**Implementation**: `ai_provider._head_tail_sample(text, max_chars)`

**Rationale**: Preserves both initialization code (top of file) and execution calls (bottom of file). Prevents feedback errors from missing `main()` calls.

**Configuration**:
- `FEEDBACK_MAX_PROMPT_FILES=8`: Max files in prompt
- `FEEDBACK_MAX_PROMPT_FILE_CHARS=24000`: Per-file char limit
- `FEEDBACK_MAX_PROMPT_TOTAL_CHARS=52000`: Total char budget

### 3. Diagnostics Tracking

**Pattern**: Track sampling decisions and persist in `AIFeedbackDraft.prompt_diagnostics` JSON field.

**Data Structure**:
```python
{
    "max_files": 8,
    "max_chars_per_file": 24000,
    "max_total_chars": 52000,
    "files_sampled": 1,
    "total_chars_used": 6382,
    "truncated": False,
    "truncated_file_count": 0,
    "truncated_files": []
}
```

**UI Display**: 
- `_sampling_diagnostics_labels()` formats as "Included 1 of 8 max files (6,382 of 52,000 chars)"
- Meta-chips in draft history show per-draft diagnostics

### 4. Batch Job Queue

**Pattern**: Async background processing with worker claiming jobs atomically.

**Job Lifecycle**:
1. User clicks batch button → `enqueue_batch_review_job()` creates `QUEUED` job
2. Worker runs `claim_next_queued_job()` → atomically transitions to `RUNNING`
3. Worker executes `run_batch_review_job()` → iterates submissions
4. Job transitions to `COMPLETED` or `FAILED` with summary message

**Worker**: `python manage.py process_batch_jobs` (handles both batch and cohort jobs)

**Polling**: JavaScript polls `assignment_batch_status` endpoint every 1.5s while job running

**Generation Modes**:
- `use_detailed_passes`: Multi-pass analysis (evidence, consistency checks)
- `use_review_pass`: Second refinement pass
- Default: Single-pass generation

### 5. Cohort Summary (Async)

**Pattern**: Same async queue pattern as batch jobs, avoids nginx timeout (60s).

**Flow**:
1. User clicks "Generate Cohort Summary" → `enqueue_cohort_summary_job()`
2. Worker claims job → `run_cohort_summary_job()`
3. Calls `generate_assignment_cohort_summary()` → collects all student feedback → AI synthesizes patterns
4. Saves to `assignment.cohort_summary_html`
5. Frontend polls `assignment_cohort_summary_status` and injects HTML when complete

**Rationale**: Summary generation can take >60s, exceeding reverse proxy timeout. Backend completes successfully but user sees timeout error. Async pattern returns immediately and polls for completion.

### 6. Rubric Support

**Pattern**: Instructor defines criteria with point levels; AI generates structured table applying rubric.

**Models**: `RubricCriterion` (name, order) → `RubricLevel` (points, description, order)

**Storage**: JSON in assignment page, saved via `save_rubric` action

**AI Integration**: `_build_rubric_block()` formats rubric text for prompt; AI returns HTML table with criterion, selected level, points, and total.

### 7. Draft History

**Pattern**: Never delete drafts; each generation creates new `AIFeedbackDraft` record.

**Metadata Tracked**:
- Provider name / model name
- Generation mode (single-pass, detailed, refinement)
- Prompt diagnostics (sampling, truncation)
- Timestamp

**UI**: "Previous Drafts" panel shows chronological list with metadata chips

**Editor Fallback**: `_editor_feedback_html()` uses `final_feedback` → `proposed_feedback` → `latest_draft.draft_feedback` → `""` to prevent blank editor.

## Testing Strategy

### Test Structure

- **`grading/tests.py`**: View-level integration tests (42 tests)
- **`grading/services/tests.py`**: Service-level unit tests

### Test Patterns

- Mock Canvas API with `unittest.mock.patch`
- Mock AI provider to avoid real API calls
- Use Django `TestCase` for database isolation
- Test both success and failure paths
- Validate state transitions (PENDING → COMPLETE → APPROVED → POSTED)

### Running Tests

```bash
python manage.py test grading.tests grading.services.tests
```

**CI**: GitHub Actions runs tests on every push/PR (`.github/workflows/ci.yml`)

## Deployment Workflow

### Local Development

```bash
python manage.py migrate
python manage.py runserver
# Separate terminal for worker:
python manage.py process_batch_jobs
```

### Production (Docker Compose)

**Components**:
- `app`: Django + gunicorn (port 18080 internal)
- `db`: PostgreSQL
- Nginx reverse proxy (external HTTPS → 127.0.0.1:18080)

**Deployment Flow**:
1. Push to `main` branch
2. GitHub Actions builds Docker image → pushes to GHCR
3. SSH to VPS → pulls image → `docker compose up -d`
4. Health check polls `/healthz/` endpoint
5. If unhealthy, workflow fails and prints logs

**Environment**: All configuration injected from GitHub Secrets (no manual `.env` management)

**Files**:
- `Dockerfile`: Multi-stage build with migrations
- `docker-compose.prod.yml`: App + DB definition
- `.github/workflows/deploy.yml`: CI/CD pipeline
- `deploy/nginx/feedback.perdrizet.org.conf`: Nginx config sample

### Database Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

**Deployment**: Migrations run automatically in Docker entrypoint.

**Recent Migrations**:
- `0008`: Added batch job generation modes (`use_detailed_passes`, `use_review_pass`)
- `0009`: Added `AIFeedbackDraft.prompt_diagnostics` JSON field
- `0010`: Added `CohortSummaryJob` model

## Development Patterns

### Adding a New Service Function

1. Implement in `grading/services/` with clear docstring
2. Add to `grading/services/__init__.py` exports if public
3. Write unit tests in `grading/services/tests.py`
4. Add integration test in `grading/tests.py` if view-facing
5. Run test suite: `python manage.py test`

### Adding a New View

1. Define function in `grading/views.py`
2. Add URL pattern in `grading/urls.py`
3. Create template in `grading/templates/grading/`
4. Add tests in `grading/tests.py`
5. Update AGENTS.md if pattern is novel

### Adding a New Model Field

1. Edit `grading/models.py`
2. Run `python manage.py makemigrations`
3. Review migration file
4. Run `python manage.py migrate`
5. Update tests to set new field or use default
6. Commit migration with code changes

### Error Handling

**Pattern**: Service functions return result objects (success/failure) rather than raising exceptions in normal flow.

**Canvas API**: Use try/except around `canvasapi` calls; store errors in `last_error` fields.

**AI Provider**: Catch JSON decode errors, API errors; return `AIDraftResult` with error state.

**User Feedback**: Use Django `messages` framework for success/info/error notifications.

## Common Gotchas

### 1. Dataclass Backward Compatibility

**Issue**: Adding required positional args to `AIDraftResult` breaks mocked tests.

**Solution**: Use `field(default_factory=dict)` for new fields in dataclasses used in tests.

### 2. Silent Truncation

**Issue**: Start-only truncation lost end-of-file execution code.

**Solution**: Head+tail sampling preserves both beginning and end. Test with long files to verify.

### 3. Blank Editor States

**Issue**: UI shows empty feedback when only draft history exists.

**Solution**: `_editor_feedback_html()` fallback chain loads latest draft if current fields empty.

### 4. Nginx Timeout on Long Operations

**Issue**: Operations >60s cause client timeout even though backend succeeds.

**Solution**: Use async job pattern with polling (see batch jobs, cohort summary).

### 5. Test Database State

**Issue**: Tests fail due to stale data or missing migrations.

**Solution**: Django creates/destroys test DB automatically. Ensure migrations committed.

## Quick Reference

### Important File Paths

- Models: `grading/models.py`
- Views: `grading/views.py`
- URLs: `grading/urls.py`
- Canvas sync: `grading/services/canvas_sync.py`
- AI provider: `grading/services/ai_provider.py`
- Batch jobs: `grading/services/batch_jobs.py`
- Cohort jobs: `grading/services/cohort_summary_jobs.py`
- Main template: `grading/templates/grading/submission_detail.html`
- Settings: `feedback_project/settings.py`
- Docker: `Dockerfile`, `docker-compose.prod.yml`
- CI/CD: `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`

### Useful Commands

```bash
# Development
python manage.py runserver
python manage.py process_batch_jobs
python manage.py test

# Database
python manage.py makemigrations
python manage.py migrate
python manage.py shell

# Docker
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f app
docker compose -f docker-compose.prod.yml exec app python manage.py migrate

# Deployment
git push origin main  # Triggers CI/CD
```

### Environment Variable Checklist

**Required**:
- `CANVAS_API_URL`
- `CANVAS_API_KEY`
- `FEEDBACK_AI_API_KEY` or `OPENAI_API_KEY`
- `FEEDBACK_SECRET_KEY`

**Optional (with defaults)**:
- `FEEDBACK_AI_BASE_URL` (default: Promptly)
- `FEEDBACK_AI_MODEL` (default: `default`)
- `FEEDBACK_AI_TEMPERATURE` (default: `0.2`)
- `FEEDBACK_MAX_PROMPT_FILES` (default: `8`)
- `FEEDBACK_MAX_PROMPT_FILE_CHARS` (default: `24000`)
- `FEEDBACK_MAX_PROMPT_TOTAL_CHARS` (default: `52000`)

**Production**:
- `FEEDBACK_DEBUG=false`
- `FEEDBACK_ALLOWED_HOSTS`
- `FEEDBACK_CSRF_TRUSTED_ORIGINS`
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`

## Related Projects

- **[canvas-instructor-tools](https://github.com/gperdrizet/canvas-instructor-tools)**: Python package for Canvas API operations (installed as dependency)
- **Version**: Currently using `0.1.0-alpha.1` (PyPI: `canvas-instructor-tools`)

## Getting Help

- **Code Search**: Use grep/semantic search to find implementation examples
- **Test Suite**: Look at `grading/tests.py` and `grading/services/tests.py` for usage patterns
- **Django Docs**: https://docs.djangoproject.com/
- **Canvas API Docs**: https://canvas.instructure.com/doc/api/
