# CVTailorAI Bot API

## TL;DR

This project is a FastAPI backend for tailoring a structured resume to a target job description.

It focuses on three jobs:

- keep `master_resume` JSON consistent;
- select the most relevant bullets, skills, and keywords for a vacancy;
- prepare resume output for ATS scoring and Google Docs formatting.

The main code is now split into:

- `app.py` - HTTP API, security, request guards, and route handlers;
- `api_models.py` - Pydantic request and response models;
- `resume_utils.py` - business logic and resume transformations.

---

## What The Service Does

The API works with structured JSON, not raw `.docx` or `.pdf` files.

Core responsibilities:

- merge confirmed terms and generated bullets into the master resume;
- detect gaps between vacancy requirements and the current resume;
- build an adapted resume with prioritized bullet points;
- normalize inconsistent resume JSON;
- convert resume JSON into custom text markup for Google Docs;
- compute ATS-like similarity metrics between a resume and a vacancy;
- support UI or bot flows for confirming terms against bullets.

This backend is best understood as a resume-tailoring engine rather than a full product UI.

---

## High-Level Flow

### 1. Input

The backend mainly works with two objects:

- `master_resume` - the source-of-truth resume in JSON form;
- `extract` - structured job requirements, usually produced by an upstream parser or another service.

### 2. Normalize

`/normalize_master` repairs and stabilizes resume structure:

- restores missing sections;
- deduplicates bullets by text;
- renumbers bullet IDs;
- rebuilds `confirmed_by`;
- rebuilds `unconfirmed`;
- captures unknown terms from bullets.

### 3. Find missing terms

`/find_gaps` compares the vacancy extract against the resume and adds missing skills or keywords into `unconfirmed`.

### 4. Generate an adapted resume

`/generate_adapted_resume`:

- measures baseline and adjusted match rates;
- ranks bullets using weighted heuristics;
- limits overload per bullet and per company;
- returns an adapted resume plus helper payloads for later editing.

### 5. Export and score

The service can then:

- turn resume JSON into Google Docs-friendly text via `/cv_to_text`;
- produce Google Docs `batchUpdate` requests via `/format_google_doc`;
- estimate ATS-like similarity via `/ats_score`.

---

## Project Structure

```text
CVTailorAI_Bot/
|- app.py
|- api_models.py
|- resume_utils.py
|- requirements.txt
|- test_integrity.py
```

---

## Main Endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /merge` | Merge confirmed terms and generated bullets into `master_resume` |
| `POST /format_google_doc` | Convert custom markup into Google Docs `batchUpdate` requests |
| `POST /find_gaps` | Add missing required skills and keywords into `unconfirmed` |
| `POST /generate_adapted_resume` | Build an adapted resume for a vacancy |
| `POST /unconfirmed_to_terms` | Convert unconfirmed terms into a flat list |
| `POST /btnsCompany` | Build company button payloads for a bot/UI |
| `POST /select_to_confirm_list` | Prepare terms and bullets for confirmation UI |
| `POST /auto_confirm` | Apply `confirmed_by` links in bulk |
| `POST /remove_duplicates` | Remove duplicate unconfirmed or unused terms |
| `POST /normalize_master` | Repair and normalize `master_resume` |
| `POST /cv_to_text` | Convert resume JSON to custom text markup |
| `POST /push_bullets` | Push edited bullet texts back into the resume |
| `POST /ats_score` | Calculate ATS-like metrics for vacancy vs resume |
| `POST /analyze_job` | Check whether terms from `extract` appear in raw job text |
| `POST /skills2master` | Move classified skills into the master resume |
| `POST /bullets2buttons` | Build button payloads from bullets |
| `POST /term_not_used` | Mark a term as explicitly not used |
| `POST /get_company_bullets` | Return bullets for one company |
| `POST /confirm_term` | Link a term to a bullet |
| `POST /add_new_bullet` | Add a new bullet and confirm the linked term |
| `GET /health` | Lightweight public healthcheck |

---

## Important Data Concepts

### `confirmed_by`

Each skill or keyword can reference bullet IDs that prove the term is used in experience.

### `unconfirmed`

Skills and keywords that exist in the resume structure but still do not have proof bullets.

### `explicitly_not_used`

Terms intentionally rejected by the user so they are not reintroduced automatically.

### `unknown.skills`

Terms found inside bullets that do not yet exist in known skill or keyword sections.

---

## Matching Logic In Plain English

The adapted resume builder uses a heuristic ranking algorithm:

- mandatory terms get the highest weight;
- nice-to-have terms get medium weight;
- rare terms get a bonus;
- bullets with too many attached terms get a soft penalty;
- each company gets a bullet cap based on employment duration;
- after trimming, the algorithm tries to restore lost important coverage.

This is practical and useful, but still heuristic rather than hard business truth.

---

## Security And Runtime Guards

The current API includes a first protection layer:

- API key protection for protected endpoints;
- Pydantic request and response models;
- request body size limiting;
- in-memory rate limiting;
- request timeout enforcement;
- sanitized internal error responses.

Protected endpoints require the `X-API-Key` header.

---

## External Dependencies

Required packages:

- `fastapi`
- `uvicorn`
- `scikit-learn`
- `numpy`
- `huggingface_hub`
- `requests`

Environment variables:

- `API_KEY` - required for protected API routes; sent via `X-API-Key`;
- `HF_TOKEN` - optional; used for semantic similarity via Hugging Face inference;
- `MAX_REQUEST_BODY_BYTES` - optional; default `1048576`;
- `RATE_LIMIT_REQUESTS` - optional; default `60`;
- `RATE_LIMIT_WINDOW_SECONDS` - optional; default `60`;
- `REQUEST_TIMEOUT_SECONDS` - optional; default `15`.

If the Hugging Face call fails, ATS scoring falls back to TF-IDF cosine similarity.

---

## Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Set an API key:

```bash
set API_KEY=replace-me
```

Start the API:

```bash
uvicorn app:app --reload
```

Verification:
```bash
python -m py_compile app.py api_models.py test_integrity.py
python -m unittest -v test_integrity.py
```
Example request:

```bash
curl -X POST "http://127.0.0.1:8000/cv_to_text" ^
  -H "Content-Type: application/json" ^
  -H "X-API-Key: replace-me" ^
  -d "{...}"
```

Run tests:

```bash
python -m unittest -v test_integrity.py
```

---

## Test Coverage In `test_integrity.py`

The current test suite checks both business logic and API-hardening behavior:

- modules import successfully;
- expected routes are registered;
- healthcheck works without auth;
- protected routes reject missing API keys;
- validation failures return structured `422` responses;
- oversized payloads return `413`;
- rate limiting returns `429`;
- timeouts return `504`;
- internal failures are sanitized into safe `500` responses;
- `normalize_master_resume` restores a consistent structure;
- missing vacancy terms are moved into `unconfirmed`;
- adapted resume generation keeps required terms and sane bullet payloads;
- text export markup is produced correctly;
- ATS scoring falls back to TF-IDF when semantic scoring is unavailable;
- adding a new bullet preserves two-way links between terms and bullets.

This is a solid baseline, but it is not yet a full contract test suite.

---

## Remaining Risks And Vulnerabilities

The biggest remaining risks are below.

### 1. Authentication is still basic

The service currently uses one shared API key.

Impact:

- no per-user identity or role separation;
- no tenant isolation;
- leaked keys grant broad access.

Recommendation:

- move to OAuth, JWT, or gateway-backed auth for production.

### 2. Validation is improved, but the nested schema is still flexible

Pydantic now validates request shape, but several nested sections still allow extra fields to preserve compatibility with existing resume payloads.

Impact:

- unknown nested fields can still enter the system;
- the data contract is safer than before, but not yet fully strict.

Recommendation:

- progressively tighten nested models once real payload variants are fully documented.

### 3. Sensitive data may be sent to a third party

`/ats_score` can send resume text and job text to Hugging Face when `HF_TOKEN` is configured.

Impact:

- resume content may leave your infrastructure;
- this may create privacy or compliance issues.

Recommendation:

- document this clearly, add explicit opt-in behavior, or move semantic scoring to an internal service.

### 4. Rate limiting is in-memory only

Rate limiting now exists, but it is process-local.

Impact:

- limits reset on restart;
- limits are not shared across multiple instances;
- distributed deployments can bypass per-process counters.

Recommendation:

- move rate limiting to a proxy or shared store such as Redis.

### 5. Timeout enforcement is best-effort

The API now returns timeouts, but some worker-thread tasks may continue briefly after the client already received a timeout response.

Impact:

- wasted compute after timeout;
- harder capacity planning under load.

Recommendation:

- move long-running work to cancellable workers or background jobs.

### 6. Data integrity risk from heavy normalization

`normalize_master_resume` can renumber bullets, rebuild links, and move terms across sections.

Impact:

- external systems that cache bullet IDs may break;
- unexpected input shapes can still produce surprising mutations.

Recommendation:

- version the schema, document invariants, and add more fixture-based regression tests.

### 7. No audit logging or mutation tracing

Impact:

- hard to investigate abuse or unintended edits;
- no clear actor history for resume mutations.

Recommendation:

- add structured request logs, correlation IDs, actor IDs, and mutation audit records.

---

## Current Limitations

- no persistence layer is included;
- no frontend is included;
- security is still single-key and stateless;
- request throttling is local-memory only;
- most business logic still lives in one very large utility module;
- there is no CI pipeline in this repository yet.

---

## Suggested Next Improvements

- split `resume_utils.py` into focused modules such as normalization, ranking, export, and ATS;
- add fixture-based endpoint contract tests;
- add a sample `master_resume.json` and `extract.json` for local debugging;
- add structured logging and audit trails;
- move auth and throttling to production-grade infrastructure;
- add a schema version to `master_resume` and `extract`;
- add a dry-run mode for normalization and resume mutation endpoints.

---

## Bottom Line

The codebase is small, understandable, and already useful as a resume-tailoring engine.

Its main strengths are:

- clear business purpose;
- compact end-to-end workflow;
- better API safety than before through typed models and request guards.

Its main weaknesses are:

- a still-flexible nested schema;
- basic API-key security rather than production-grade auth;
- limited observability;
- a lot of logic concentrated in one large utility module.
