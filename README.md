# CVTailorAI Bot API

## TL;DR

This project is a small FastAPI backend that helps adapt a structured resume to a specific job description.

It does three main things:

- keeps a `master_resume` JSON in a consistent state;
- selects the most relevant bullets, skills, and keywords for a target vacancy;
- prepares output for ATS scoring and Google Docs formatting.

The whole project currently lives in two main files:

- `app.py` - HTTP API layer;
- `resume_utils.py` - business logic and data transformation.

---

## What The Service Does

The API expects structured JSON rather than raw files.

Core responsibilities:

- merge new confirmed terms and generated bullets into the master resume;
- detect gaps between a vacancy extract and the current master resume;
- build an adapted resume with prioritized bullets;
- normalize inconsistent resume JSON;
- convert resume JSON into text markup for Google Docs;
- compute ATS-like similarity metrics between a vacancy and a resume;
- help a client UI or bot confirm skills and keywords against bullets.

This means the service is best viewed as a resume-tailoring engine, not as a full product UI.

---

## High-Level Flow

### 1. Input data

The backend works with two main objects:

- `master_resume` - the source-of-truth resume in JSON form;
- `extract` - structured job requirements, usually prepared by an upstream parser or another service.

### 2. Normalize

`/normalize_master` cleans and stabilizes the resume:

- restores missing sections;
- deduplicates bullets by text;
- renumbers bullet IDs;
- recalculates `confirmed_by`;
- rebuilds `unconfirmed`;
- collects unknown terms from bullets.

### 3. Find missing terms

`/find_gaps` compares vacancy requirements with the resume and adds missing skills or keywords into `unconfirmed`.

### 4. Generate an adapted resume

`/generate_adapted_resume`:

- measures baseline and adjusted term match;
- selects the best bullets using weighted heuristics;
- limits overload per bullet and per company;
- returns the adapted resume plus helper payloads for later editing.

### 5. Export and score

The service can then:

- turn the resume into Google Docs-friendly text via `/cv_to_text`;
- produce Google Docs formatting requests via `/format_google_doc`;
- estimate ATS-style similarity via `/ats_score`.

---

## Project Structure

```text
CVTailorAI_Bot/
|- app.py
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
| `POST /analyze_job` | Check whether terms from `extract` are present in raw job text |
| `POST /skills2master` | Move classified skills into the master resume |
| `POST /bullets2buttons` | Build button payloads from bullets |
| `POST /term_not_used` | Mark a term as explicitly not used |
| `POST /get_company_bullets` | Return bullets for one company |
| `POST /confirm_term` | Link a term to a bullet |
| `POST /add_new_bullet` | Add a new bullet and confirm the linked term |

---

## Important Data Concepts

### `confirmed_by`

Each skill or keyword can point to bullet IDs that prove it is used in experience.

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

This is a useful ranking strategy, but it is still heuristic rather than deterministic business truth.

---

## External Dependencies

Required packages are listed in `requirements.txt`:

- `fastapi`
- `uvicorn`
- `scikit-learn`
- `numpy`
- `huggingface_hub`
- `requests`

Optional environment variable:

- `HF_TOKEN` - used for semantic similarity via Hugging Face inference.

If the Hugging Face call fails, ATS scoring falls back to TF-IDF cosine similarity.

---

## Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the API:

```bash
uvicorn app:app --reload
```

Run integrity tests:

```bash
python -m unittest -v test_integrity.py
```

---

## Test Coverage Added In `test_integrity.py`

The included test file checks the most important integrity signals without needing network access:

- modules import successfully;
- expected API routes are registered;
- `normalize_master_resume` restores a consistent structure;
- missing vacancy terms are moved into `unconfirmed`;
- adapted resume generation keeps required terms and sane bullet payloads;
- text export markup is produced correctly;
- ATS scoring works even when semantic API scoring falls back to TF-IDF;
- adding a new bullet preserves two-way links between terms and bullets.

This is not full coverage, but it is a good baseline regression suite for the current codebase.

---

## Known Vulnerabilities And Risks

These are the main issues visible in the current implementation.

### 1. No authentication or authorization

All endpoints are open in `app.py`.

Impact:

- anyone who can reach the service can call resume mutation endpoints;
- if deployed publicly, a third party could modify or process resume data without restriction.

Recommendation:

- add API key, OAuth, JWT, or gateway-level protection before public deployment.

### 2. No request schema validation

Most handlers call `await request.json()` and then pull fields with `.get(...)` instead of using Pydantic models.

Impact:

- malformed bodies can silently produce inconsistent state;
- nested shape errors may fail only at runtime;
- input contracts are hard to reason about and easy to break.

Recommendation:

- replace raw dict handling with request and response models.

### 3. Sensitive data may be sent to a third party

`/ats_score` can send resume text and job text to Hugging Face inference when `HF_TOKEN` is configured.

Impact:

- resume content may leave your infrastructure;
- this can create privacy, compliance, or client confidentiality issues.

Recommendation:

- document this clearly, add an opt-in flag, or move semantic scoring to a controlled internal service.

### 4. Potential denial-of-service through large payloads

The service accepts arbitrary JSON bodies and can run expensive text/vector operations on them.

Impact:

- very large `resume_text`, `job_text`, or resume JSON payloads can cause heavy CPU or memory usage;
- repeated calls may degrade the service.

Recommendation:

- add body size limits, request timeouts, rate limiting, and payload validation.

### 5. Raw exception messages are returned to clients

Several endpoints expose `str(e)` in HTTP responses.

Impact:

- internal implementation details can leak to clients;
- error text can reveal data shape assumptions and internal logic.

Recommendation:

- return stable user-safe error messages and log internal exceptions server-side.

### 6. Data integrity risk from heavy in-place normalization

`normalize_master_resume` can renumber bullets, rebuild links, and move terms across sections.

Impact:

- external systems that cache bullet IDs may break;
- unexpected input shapes can produce surprising mutations;
- the function is powerful, but also easy to misuse in a larger workflow.

Recommendation:

- version the schema, document invariants, and add more fixture-based tests.

### 7. No built-in rate limiting, audit logging, or access tracing

Impact:

- hard to investigate abuse or unintended use;
- no clear per-user accountability for resume mutations.

Recommendation:

- add structured request logs, correlation IDs, and rate limiting at API or proxy level.

---

## Current Limitations

- no README or API contract existed originally;
- no CI or automated test suite existed originally;
- no persistence layer is included;
- no frontend is included;
- no typed request/response models;
- no dedicated security layer.

---

## Suggested Next Improvements

- add Pydantic models for every endpoint;
- add authentication before any public deployment;
- add request size limits and throttling;
- split `resume_utils.py` into smaller focused modules;
- add fixture-based tests for each endpoint contract;
- add a sample `master_resume.json` and sample `extract.json` for local debugging;
- add structured logging and safer error handling.

---

## Bottom Line

The codebase is small, understandable, and already useful as a resume-tailoring engine.

Its main strengths are:

- compact logic;
- clear business purpose;
- practical end-to-end workflow for resume adaptation.

Its main weaknesses are:

- weak input validation;
- no security boundary;
- limited test coverage;
- a lot of logic concentrated in one large utility module.
