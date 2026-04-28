# Ashby Application Metadata — GraphQL Contract

**Captured:** 2026-04-28  
**Status:** CONFIRMED — all queries work against production

---

## Background

The spec-listed REST endpoint `GET https://api.ashbyhq.com/posting-api/job-posting/{uuid}`
returns **401 Unauthorized** — that is the protected partner/partner-API endpoint.
The public endpoint that powers hosted Ashby job boards is a **GraphQL** API at:

```
POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting
```

---

## Step 1: Get a real job ID

The per-company public job list IS available via REST:

```
GET https://api.ashbyhq.com/posting-api/job-board/{org}
```

Returns `{ "jobs": [{ "id": "<uuid>", "jobUrl": "...", "title": "..." }, ...] }`.
This is unauthenticated. Use `org` = the slug from the Ashby hosted board URL, e.g.
`https://jobs.ashbyhq.com/ashby` → org = `ashby`.

---

## Endpoint + Method

```
POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting
Content-Type: application/json
```

No auth headers required.

---

## Operation Name

`ApiJobPosting`

---

## Full GraphQL Query

```graphql
query ApiJobPosting(
  $organizationHostedJobsPageName: String!,
  $jobPostingId: String!
) {
  jobPosting(
    organizationHostedJobsPageName: $organizationHostedJobsPageName,
    jobPostingId: $jobPostingId
  ) {
    id
    title
    applicationForm {
      sections {
        title
        descriptionHtml
        fieldEntries {
          id
          isRequired
          descriptionHtml
          field
        }
      }
    }
  }
}
```

**Key finding:** `field` is a `JSON!` scalar (not a typed sub-object), so it must be
queried as a leaf node — no sub-selection. It contains the full field definition
including type, title, and selectableValues.

---

## Variables Structure

```json
{
  "organizationHostedJobsPageName": "ashby",
  "jobPostingId": "145ff46b-1441-4773-bcd3-c8c90baa598a"
}
```

- `organizationHostedJobsPageName` — the org slug (matches the URL path on jobs.ashbyhq.com)
- `jobPostingId` — the UUID from the posting-api/job-board list endpoint

---

## Sample Response

Trimmed from real response (`ashby` org, job = "Engineer Who Can Design, Americas"):

```json
{
  "data": {
    "jobPosting": {
      "id": "145ff46b-1441-4773-bcd3-c8c90baa598a",
      "title": "Engineer Who Can Design, Americas",
      "applicationForm": {
        "sections": [
          {
            "title": "Basic Info",
            "descriptionHtml": null,
            "fieldEntries": [
              {
                "id": "b81232f8-...__systemfield_name",
                "isRequired": true,
                "descriptionHtml": null,
                "field": {
                  "id": "c8984924-...",
                  "path": "_systemfield_name",
                  "title": "Name",
                  "type": "String",
                  "__autoSerializationID": "StringField"
                }
              },
              {
                "id": "b81232f8-...__systemfield_resume",
                "isRequired": false,
                "descriptionHtml": null,
                "field": {
                  "path": "_systemfield_resume",
                  "title": "Resume",
                  "type": "File",
                  "__autoSerializationID": "FileField"
                }
              }
            ]
          }
        ]
      }
    }
  }
}
```

Sample with `ValueSelect` field (from Notion org):

```json
{
  "id": "...",
  "isRequired": false,
  "descriptionHtml": null,
  "field": {
    "title": "What pronouns would you like our team to use when addressing you?",
    "type": "ValueSelect",
    "selectableValues": [
      {"label": "He/Him", "value": "He/Him"},
      {"label": "She/Her", "value": "She/Her"},
      {"label": "They/Them", "value": "They/Them"},
      {"label": "Prefer not to say", "value": "Prefer not to say"}
    ],
    "__autoSerializationID": "ValueSelectField"
  }
}
```

Sample with `MultiValueSelect` field (from Notion org):

```json
{
  "field": {
    "title": "How did you hear about this opportunity? (select all that apply)",
    "type": "MultiValueSelect",
    "selectableValues": [
      {"label": "LinkedIn", "value": "LinkedIn"},
      {"label": "Glassdoor", "value": "Glassdoor"}
    ],
    "__autoSerializationID": "MultiValueSelectField"
  }
}
```

---

## Field Type Inventory

All confirmed field types from live sampling across multiple orgs (ashby, linear, ramp, notion):

| Ashby `type` | `__autoSerializationID` | Normalized to |
|---|---|---|
| `String` | `StringField` | `text` |
| `Email` | `EmailField` | `text` |
| `Phone` | `PhoneField` | `text` |
| `LongText` | `LongTextField` | `textarea` |
| `File` | `FileField` | `file` |
| `Boolean` | `BooleanField` | `yes_no` |
| `Location` | `LocationField` | `text` |
| `ValueSelect` | `ValueSelectField` | `select` |
| `MultiValueSelect` | `MultiValueSelectField` | `multi_select` |

---

## Cover Letter Detection

- Ashby has no dedicated cover_letter field_name like Greenhouse
- Detected by checking `field.path == "_systemfield_cover_letter"` OR by heuristic match on `field.title` containing "cover letter" (case-insensitive)
- Linear org uses `type=LongText`, `title="Cover letter"` — no dedicated system field path
- Some orgs use `path="_systemfield_cover_letter"` for resume-equivalent file field

---

## "Not Found" Handling

When `jobPostingId` does not exist, the API returns HTTP **200** with:

```json
{ "data": { "jobPosting": null } }
```

**Not** an HTTP 404. The fetcher must check `data.jobPosting is null` and raise
`AshbyFetchError(reason="job_no_longer_available")`.

---

## Edge Cases

- `section.title` can be `null` (seen in Ramp, Notion) — safe to omit/ignore
- `field.selectableValues` only present for `ValueSelect` and `MultiValueSelect` fields
- `descriptionHtml` at both section and fieldEntry level — usually `null`; used as description
- `field.path` contains the stable field ID (used as `field_name` in normalized output)
- `field.path` starts with `_systemfield_` for system fields (name, email, resume, cover_letter, location)
- `isMany` on field is always `false` in observed data; `MultiValueSelect` has `isMany=false` but type implies multi

---

## Rate Limits

No rate limiting observed during investigation (multiple requests in quick succession).
Ashby does not advertise a public rate limit for this endpoint. Standard 10s timeout is appropriate.
