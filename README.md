# feedback

Django-based instructor interface for AI-assisted grading workflows on Canvas assignments.

## Current MVP Scope

- Sync assignment submissions from Canvas using `canvas-instructor-tools`
- Download file attachments and URL submissions into local storage
- Generate draft AI feedback (provider contract ready; placeholder implementation included)
- Require instructor approval before posting grade/comment
- Post approved outcomes back to Canvas

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

- `CANVAS_API_URL`: Canvas base URL
- `CANVAS_API_KEY`: Canvas API token
- `FEEDBACK_AI_API_KEY`: Promptly API key (`sk-...`)
- `FEEDBACK_AI_BASE_URL`: defaults to `https://promptlyapi.com/v1`
- `FEEDBACK_AI_MODEL`: defaults to `default` (Promptly accepts but may ignore)
- `FEEDBACK_AI_TEMPERATURE`: optional, defaults to `0.2`
- `FEEDBACK_SECRET_KEY`: Django secret key override
- `FEEDBACK_DEBUG`: `true`/`false`
- `FEEDBACK_ALLOWED_HOSTS`: comma-separated hosts

You can also use `OPENAI_API_KEY` as a fallback if `FEEDBACK_AI_API_KEY` is not set.

## Workflow

1. Sync an assignment by Canvas `course_id` and `assignment_id`.
2. Open a submission and generate AI draft feedback.
3. Edit score/feedback as needed.
4. Approve the submission.
5. Post to Canvas.

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
