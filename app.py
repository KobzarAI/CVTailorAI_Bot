from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool

from api_models import (
    ATSMetricsResponseModel,
    ATSScoreRequestModel,
    AddNewBulletRequestModel,
    AnalyzeJobRequestModel,
    AnalyzeJobResponseModel,
    AutoConfirmRequestModel,
    BulletsToButtonsRequestModel,
    CompaniesRequestModel,
    CompanyBulletsResponseModel,
    ConfirmTermRequestModel,
    FindGapsRequestModel,
    GenerateAdaptedResumeRequestModel,
    GenerateAdaptedResumeResponseModel,
    GeneratedTermsResponseModel,
    GetCompanyBulletsRequestModel,
    GoogleDocRequestModel,
    GoogleDocResponseModel,
    HealthResponseModel,
    InlineKeyboardResponseModel,
    MasterResumeModel,
    MasterResumePayloadModel,
    MergeRequestModel,
    PushBulletsRequestModel,
    RemoveDuplicatesRequestModel,
    SelectToConfirmResponseModel,
    SkillsToMasterRequestModel,
    TermNotUsedRequestModel,
    TextResponseModel,
)
from resume_utils import (
    BulletsToButtons,
    GetCompanyBullets,
    Term_not_used,
    add_new_bullet,
    analyze_job_description,
    auto_confirm_terms,
    btnsCompany,
    calculate_match_percent,
    compute_ats_metrics,
    confirm_term,
    cv2text,
    extract_bullets,
    filter_and_rank_bullets,
    find_gaps_and_update_master,
    format_google_doc_content,
    gather_all_current_terms,
    gather_origin_terms,
    match_terms,
    merge_jsons,
    normalize_master_resume,
    push_bullets,
    remove_unconfirmed_and_unused_terms,
    select_to_confirm_list,
    simplify_extract,
    skills2master,
    unconfirmed2terms,
)


logger = logging.getLogger(__name__)

app = FastAPI(title="CVTailorAI Bot API", version="1.1.0")


def _get_int_env(name: str, default: int, min_value: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(int(raw_value), min_value)
    except ValueError:
        logger.warning("Invalid integer value for %s=%r. Falling back to %s.", name, raw_value, default)
        return default


def _get_float_env(name: str, default: float, min_value: float = 0.001) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(float(raw_value), min_value)
    except ValueError:
        logger.warning("Invalid float value for %s=%r. Falling back to %s.", name, raw_value, default)
        return default


def _client_identifier(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _model_to_dict(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, exclude_none=True)
    return model


def _generate_adapted_resume_payload(extract: dict[str, Any], extended_master_resume: dict[str, Any]) -> dict[str, Any]:
    origin_skills, origin_keywords = gather_origin_terms(extended_master_resume)
    base_skill_matches, base_skill_total = match_terms(extract.get("required_skills", []), origin_skills)
    base_keyword_matches, base_keyword_total = match_terms(extract.get("required_keywords", []), origin_keywords)
    match_base = calculate_match_percent(base_skill_matches + base_keyword_matches, base_skill_total + base_keyword_total)

    all_skills, all_keywords = gather_all_current_terms(extended_master_resume)
    adj_skill_matches, adj_skill_total = match_terms(extract.get("required_skills", []), all_skills)
    adj_keyword_matches, adj_keyword_total = match_terms(extract.get("required_keywords", []), all_keywords)
    match_adjusted = calculate_match_percent(adj_skill_matches + adj_keyword_matches, adj_skill_total + adj_keyword_total)

    adapted_resume = filter_and_rank_bullets(extended_master_resume, extract)
    short_extract = simplify_extract(extract)
    bullets = extract_bullets(adapted_resume)

    return {
        "adapted_resume": adapted_resume,
        "match_base": match_base,
        "match_adjusted": match_adjusted,
        "short_extract": short_extract,
        "bullets": bullets,
    }


def require_api_key(api_key: str | None) -> None:
    expected_key = os.getenv("API_KEY")

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured.",
        )

    if not api_key or not secrets.compare_digest(api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_id: str, path: str, limit: int, window_seconds: float) -> tuple[bool, float]:
        now = time.time()
        key = (client_id, path)

        with self._lock:
            history = self._events[key]
            while history and now - history[0] >= window_seconds:
                history.popleft()

            if len(history) >= limit:
                retry_after = max(window_seconds - (now - history[0]), 0.0)
                return False, retry_after

            history.append(now)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = InMemoryRateLimiter()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def api_key_dependency(api_key: str | None = Security(api_key_header)) -> None:
    require_api_key(api_key)


SECURED_ROUTE_DEPENDENCIES = [Depends(api_key_dependency)]


@app.middleware("http")
async def hardening_middleware(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        max_request_body_bytes = _get_int_env("MAX_REQUEST_BODY_BYTES", 1_048_576)

        if len(body) > max_request_body_bytes:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "detail": "Request body is too large.",
                    "error_code": "request_too_large",
                },
            )

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive

        limit = _get_int_env("RATE_LIMIT_REQUESTS", 60)
        window_seconds = _get_float_env("RATE_LIMIT_WINDOW_SECONDS", 60.0)
        allowed, retry_after = rate_limiter.allow(_client_identifier(request), request.url.path, limit, window_seconds)

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(max(1, int(retry_after) or 1))},
                content={
                    "detail": "Rate limit exceeded. Please retry later.",
                    "error_code": "rate_limit_exceeded",
                },
            )

    timeout_seconds = _get_float_env("REQUEST_TIMEOUT_SECONDS", 15.0)
    try:
        return await asyncio.wait_for(call_next(request), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={
                "detail": "Request processing timed out.",
                "error_code": "request_timeout",
            },
        )
    except Exception as exc:
        logger.exception("Unhandled exception while processing %s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Internal server error.",
                "error_code": "internal_server_error",
            },
        )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed.",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(ValueError)
async def value_error_exception_handler(request: Request, exc: ValueError):
    logger.warning("Value error while processing %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": "Request data is invalid.",
            "error_code": "invalid_request",
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception while processing %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error.",
            "error_code": "internal_server_error",
        },
    )


@app.get("/health", response_model=HealthResponseModel)
async def healthcheck() -> HealthResponseModel:
    return HealthResponseModel(status="ok")


@app.post("/merge", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def merge_endpoint(payload: MergeRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(merge_jsons, _model_to_dict(payload.json1), _model_to_dict(payload.json2))


@app.post("/format_google_doc", response_model=GoogleDocResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def format_google_doc(payload: GoogleDocRequestModel) -> GoogleDocResponseModel:
    return await run_in_threadpool(format_google_doc_content, _model_to_dict(payload))


@app.post("/find_gaps", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def find_gaps_endpoint(payload: FindGapsRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(find_gaps_and_update_master, _model_to_dict(payload.extract), _model_to_dict(payload.master_resume))


@app.post("/generate_adapted_resume", response_model=GenerateAdaptedResumeResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def generate_adapted_resume_endpoint(payload: GenerateAdaptedResumeRequestModel) -> GenerateAdaptedResumeResponseModel:
    return await run_in_threadpool(
        _generate_adapted_resume_payload,
        _model_to_dict(payload.extract),
        _model_to_dict(payload.extended_master_resume),
    )


@app.post("/unconfirmed_to_terms", response_model=GeneratedTermsResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def unconfirmed_to_terms_endpoint(payload: MasterResumePayloadModel) -> GeneratedTermsResponseModel:
    return await run_in_threadpool(unconfirmed2terms, _model_to_dict(payload))


@app.post("/btnsCompany", response_model=InlineKeyboardResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def btnsCompany_endpoint(payload: CompaniesRequestModel) -> InlineKeyboardResponseModel:
    return await run_in_threadpool(btnsCompany, _model_to_dict(payload))


@app.post("/select_to_confirm_list", response_model=SelectToConfirmResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def select_to_confirm_list_endpoint(payload: MasterResumeModel) -> SelectToConfirmResponseModel:
    return await run_in_threadpool(select_to_confirm_list, _model_to_dict(payload))


@app.post("/auto_confirm", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def auto_confirm_terms_endpoint(payload: AutoConfirmRequestModel) -> MasterResumeModel:
    to_confirm_payload = {"ToConfirm_list": [item.model_dump(exclude_none=True) for item in payload.ToConfirm_list]}
    return await run_in_threadpool(auto_confirm_terms, _model_to_dict(payload.master_resume), to_confirm_payload)


@app.post("/remove_duplicates", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def remove_unconfirmed_and_unused_endpoint(payload: RemoveDuplicatesRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(
        remove_unconfirmed_and_unused_terms,
        payload.duplicates,
        _model_to_dict(payload.master_resume),
    )


@app.post("/normalize_master", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def normalize_master_resume_endpoint(payload: MasterResumeModel) -> MasterResumeModel:
    return await run_in_threadpool(normalize_master_resume, _model_to_dict(payload))


@app.post("/cv_to_text", response_model=TextResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def cv_to_text_endpoint(payload: MasterResumeModel) -> TextResponseModel:
    return await run_in_threadpool(cv2text, _model_to_dict(payload))


@app.post("/push_bullets", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def push_bullets_endpoint(payload: PushBulletsRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(push_bullets, _model_to_dict(payload))


@app.post("/ats_score", response_model=ATSMetricsResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def ats_score(payload: ATSScoreRequestModel) -> ATSMetricsResponseModel:
    return await run_in_threadpool(compute_ats_metrics, payload.job_text, payload.resume_text)


@app.post("/analyze_job", response_model=AnalyzeJobResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def analyze_job_endpoint(payload: AnalyzeJobRequestModel) -> AnalyzeJobResponseModel:
    return await run_in_threadpool(analyze_job_description, payload.job_description, _model_to_dict(payload.extract))


@app.post("/skills2master", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def skills2master_endpoint(payload: SkillsToMasterRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(skills2master, _model_to_dict(payload.skills), _model_to_dict(payload.master_resume))


@app.post("/bullets2buttons", response_model=InlineKeyboardResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def bullets2buttons(payload: BulletsToButtonsRequestModel) -> InlineKeyboardResponseModel:
    return await run_in_threadpool(BulletsToButtons, _model_to_dict(payload))


@app.post("/term_not_used", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def term_not_used_endpoint(payload: TermNotUsedRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(
        Term_not_used,
        payload.term_name,
        payload.term_type,
        _model_to_dict(payload.master_resume),
    )


@app.post("/get_company_bullets", response_model=CompanyBulletsResponseModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def get_company_bullets_endpoint(payload: GetCompanyBulletsRequestModel) -> CompanyBulletsResponseModel:
    return await run_in_threadpool(
        GetCompanyBullets,
        _model_to_dict(payload.master_resume),
        payload.company_name,
    )


@app.post("/confirm_term", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def confirm_term_endpoint(payload: ConfirmTermRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(
        confirm_term,
        _model_to_dict(payload.master_resume),
        payload.bullet_id,
        payload.term_name,
        payload.term_type,
    )


@app.post("/add_new_bullet", response_model=MasterResumeModel, dependencies=SECURED_ROUTE_DEPENDENCIES)
async def add_new_bullet_endpoint(payload: AddNewBulletRequestModel) -> MasterResumeModel:
    return await run_in_threadpool(
        add_new_bullet,
        _model_to_dict(payload.master_resume),
        payload.company,
        payload.bullet,
        payload.term_name,
        payload.term_type,
    )
