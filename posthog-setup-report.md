<wizard-report>
# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics into NaukriBaba's FastAPI backend (`app.py`). The `posthog` Python SDK is initialized on app startup using the instance-based `Posthog()` constructor, reads credentials from environment variables (`POSTHOG_API_KEY`, `POSTHOG_HOST`), and registers a graceful shutdown hook via `atexit`. Exception autocapture is enabled. Twelve business-critical server-side events are now tracked across the core user flows — scoring, tailoring, cover letters, contacts, pipeline runs, application tracking, feedback, and onboarding.

| Event | Description | File |
|---|---|---|
| `job_scored` | User scores a JD against a base resume via `/api/score` | `app.py` |
| `resume_tailor_started` | User queues a resume tailoring task via `/api/tailor` | `app.py` |
| `cover_letter_started` | User queues a cover letter generation task via `/api/cover-letter` | `app.py` |
| `contacts_search_started` | User queues a LinkedIn contacts search via `/api/contacts` | `app.py` |
| `pipeline_started` | User triggers the daily job-hunting pipeline via `/api/pipeline/run` | `app.py` |
| `pipeline_single_job_started` | User adds a single job via `/api/pipeline/run-single` | `app.py` |
| `resume_uploaded` | User uploads a PDF resume via `/api/resumes/upload` | `app.py` |
| `job_status_updated` | User updates a job's application status (Applied, Interview, Offer, etc.) | `app.py` |
| `timeline_event_added` | User adds an event to a job's application timeline | `app.py` |
| `email_template_generated` | User generates a cold outreach / follow-up / thank-you email via AI | `app.py` |
| `score_flagged` | User flags an AI-generated score as inaccurate | `app.py` |
| `profile_updated` | User updates their profile fields | `app.py` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

- **Dashboard — Analytics basics**: https://eu.posthog.com/project/167813/dashboard/647045
- **Resume & Cover Letter Generation Funnel**: https://eu.posthog.com/project/167813/insights/dyGGP8sO
- **Core Actions — Daily Trend**: https://eu.posthog.com/project/167813/insights/jVVQ4hSh
- **Application Status Updates Breakdown**: https://eu.posthog.com/project/167813/insights/jT1gNGt8
- **Score Flags vs Emails Generated**: https://eu.posthog.com/project/167813/insights/CXvU0qd1
- **Resume Upload & Profile Completion**: https://eu.posthog.com/project/167813/insights/hD7TpktN

### Agent skill

We've left an agent skill folder in your project. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.

</wizard-report>
