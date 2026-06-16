# feedback

Django-based instructor interface for AI-assisted grading workflows on Canvas assignments. Uses [canvas-instructor-tools](https://github.com/gperdrizet/canvas-instructor-tools) for Canvas API interactions and integrates with OpenAI-compatible AI providers (Promptly, OpenAI, etc.).

## Features

- **Canvas Integration**: Sync assignments, download submissions (files + URL content), and post grades/comments back
- **AI Feedback Generation**: Generate draft feedback using OpenAI-compatible providers with rubric-aware scoring
- **Smart Sampling**: Head+tail file truncation preserves beginning and end of long files (configurable limits)
- **Batch Processing**: Background job queue for bulk draft generation across all students
- **Cohort Summary**: Async generation of assignment-level analysis from student feedback
- **Approval Workflow**: Require instructor review before posting any grade/comment to Canvas
- **Diagnostics**: Track file sampling, truncation, and prompt token usage per draft
- **Unsubmitted Handling**: Automatically skip students with no submission
- **Multi-pass Generation**: Optional detailed analysis and refinement passes
- **Draft History**: Review all previous AI-generated drafts with metadata (provider, model, mode)
- **Rubric Support**: Define scoring criteria with point scales; AI applies rubric in structured table format

## Setup

1. Create and activate a virtual environment in this directory.
2. Install dependencies:

	```bash
	pip install django
	pip install -e ../canvas-instructor-tools
	```

3. Run migrations:

	```bash
	python manage.py migrate
	```

4. Start the app:

	```bash
	python manage.py runserver
	```

5. Open the gradebook at `http://127.0.0.1:8000/`.

## Environment Variables

### Canvas Configuration

- `CANVAS_API_URL`: Canvas base URL
- `CANVAS_API_KEY`: Canvas API token

### AI Provider Configuration

- `FEEDBACK_AI_API_KEY`: Promptly API key (`sk-...`)
- `FEEDBACK_AI_BASE_URL`: defaults to `https://promptlyapi.com/v1`
- `FEEDBACK_AI_MODEL`: defaults to `default` (Promptly accepts but may ignore)
- `FEEDBACK_AI_TEMPERATURE`: optional, defaults to `0.2`
- `OPENAI_API_KEY`: fallback if `FEEDBACK_AI_API_KEY` is not set

### Prompt Sampling Limits

- `FEEDBACK_MAX_PROMPT_FILES`: max files to include (default: `8`)
- `FEEDBACK_MAX_PROMPT_FILE_CHARS`: max chars per file (default: `24000`)
- `FEEDBACK_MAX_PROMPT_TOTAL_CHARS`: total char budget (default: `52000`)

### Django Configuration

- `FEEDBACK_SECRET_KEY`: Django secret key override
- `FEEDBACK_DEBUG`: `true`/`false`
- `FEEDBACK_ALLOWED_HOSTS`: comma-separated hosts
- `FEEDBACK_CSRF_TRUSTED_ORIGINS`: for HTTPS deployments
- `FEEDBACK_BIND_PORT`: Docker bind port (default: `18080`)

### Database Configuration (Docker)

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`

## Workflow

### Individual Submission

1. Sync an assignment by Canvas `course_id` and `assignment_id`
2. Open a submission and generate AI draft feedback
3. Review diagnostics (files included, truncation status)
4. Edit score/feedback as needed
5. Approve and post to Canvas

### Batch Processing

1. Configure rubric and additional instructions on assignment page
2. Click "Batch Download & Generate Drafts" (optional: enable multi-pass or refinement)
3. Background worker processes all submissions
4. Monitor progress with live polling UI
5. Review and approve individual submissions

### Cohort Summary

1. Generate student feedback drafts first
2. Click "Generate Cohort Summary (Background)"
3. AI analyzes all feedback to identify common patterns, strengths, and mistakes
4. Summary appears automatically when generation completes

### Background Worker

Run the worker process to handle batch and cohort summary jobs:

```bash
python manage.py process_batch_jobs
```

For production, run as a systemd service or Docker container.

## Docker Deployment (VPS)

Target deployment path on VPS: `/opt/feedback`

This repository includes:

- `Dockerfile` for the Django app (gunicorn)
- `docker-compose.prod.yml` with app + PostgreSQL
- `deploy/bootstrap-vps.sh` first-run VPS bootstrap helper (safe if Docker already exists)
- GitHub Actions workflows:
	- `.github/workflows/ci.yml`
	- `.github/workflows/deploy.yml`

### Nginx reverse proxy

Use the sample config at `deploy/nginx/feedback.perdrizet.org.conf` and point it to
`127.0.0.1:18080` (or your configured `FEEDBACK_BIND_PORT`).

### GitHub Secrets for Deploy

The deploy workflow injects all runtime configuration from GitHub Secrets and writes
`/opt/feedback/.env` automatically at deploy time (no manual `.env` management required).

Required repository secrets:

- `VPS_HOST`
- `VPS_USER`
- `VPS_SSH_KEY`
- `GHCR_USERNAME`
- `GHCR_PAT`
- `FEEDBACK_SECRET_KEY`
- `FEEDBACK_ALLOWED_HOSTS` (for example: `feedback.perdrizet.org`)
- `FEEDBACK_CSRF_TRUSTED_ORIGINS` (for example: `https://feedback.perdrizet.org`)
- `FEEDBACK_AI_API_KEY`
- `FEEDBACK_AI_BASE_URL` (for Promptly: `https://promptlyapi.com/v1`)
- `FEEDBACK_AI_MODEL` (for Promptly: `default`)
- `FEEDBACK_AI_TEMPERATURE` (for example: `0.2`)
- `CANVAS_API_URL`
- `CANVAS_API_KEY`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `FEEDBACK_BIND_PORT` (for example: `18080`)

Deployments run on push to `main` and via manual dispatch in GitHub Actions.

### Post-deploy health check behavior

Deploy runs `docker compose up -d`, then checks `http://127.0.0.1:${FEEDBACK_BIND_PORT}/healthz/` in a retry loop.

- In the current single-instance setup, this is a *health gate*, not a traffic switch.
- It verifies the updated app actually boots and serves requests.
- If unhealthy, the workflow fails and prints app logs for debugging.

For true no-downtime traffic switching, you would run a blue/green pattern with two app stacks and change the nginx upstream only after the new stack is healthy.
