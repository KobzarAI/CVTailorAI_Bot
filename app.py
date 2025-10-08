from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import json
from resume_utils import (
    merge_jsons,
    format_google_doc_content,
    term_in_list,
    find_gaps_and_update_master,
    filter_and_rank_bullets,
    match_terms,
    calculate_match_percent,
    gather_origin_terms,
    gather_all_current_terms,
    unconfirmed2terms,
    buttons,
    select_to_confirm_list,
    auto_confirm_terms,
    remove_unconfirmed_and_unused_terms,
    normalize_master_resume
)

app = FastAPI()

@app.post("/merge")
async def merge_endpoint(request: Request):
    data = await request.json()
    json1 = data.get("json1")
    json2 = data.get("json2")
    try:
        result = merge_jsons(json1, json2)
        return JSONResponse(content=result)
    except HTTPException as e:
        # FastAPI сам обработает HTTPException и вернёт detail клиенту
        raise e
    
@app.post("/format_google_doc")
async def format_google_doc(request: Request):
    data = await request.json()
    result = format_google_doc_content(data)
    return JSONResponse(content=result)

@app.post("/find_gaps")
async def find_gaps_endpoint(request: Request):
    data = await request.json()
    extract = data.get("extract")
    master_resume = data.get("master_resume")

    if extract is None or master_resume is None:
        raise HTTPException(status_code=400, detail="Missing 'extract' or 'master_resume' in request body")

    updated_master = find_gaps_and_update_master(extract, master_resume)
    return JSONResponse(content=updated_master)


@app.post("/generate_adapted_resume")
async def generate_adapted_resume_endpoint(request: Request):
    data = await request.json()
    extract = data.get("extract")
    extended_master_resume = data.get("extended_master_resume")

    if extract is None or extended_master_resume is None:
        raise HTTPException(
            status_code=400,
            detail="Missing 'extract' or 'extended_master_resume' in request body"
        )

    # Gather origin-term lists for base match calculation
    origin_skills, origin_keywords = gather_origin_terms(extended_master_resume)
    base_skill_matches, base_skill_total = match_terms(extract.get("required_skills", []), origin_skills)
    base_keyword_matches, base_keyword_total = match_terms(extract.get("required_keywords", []), origin_keywords)
    match_base = calculate_match_percent(base_skill_matches + base_keyword_matches,
                                         base_skill_total + base_keyword_total)

    # Gather all current terms for adjusted match calculation
    all_skills, all_keywords = gather_all_current_terms(extended_master_resume)
    adj_skill_matches, adj_skill_total = match_terms(extract.get("required_skills", []), all_skills)
    adj_keyword_matches, adj_keyword_total = match_terms(extract.get("required_keywords", []), all_keywords)
    match_adjusted = calculate_match_percent(adj_skill_matches + adj_keyword_matches,
                                             adj_skill_total + adj_keyword_total)

    adapted_resume = filter_and_rank_bullets(extended_master_resume, extract)

    return JSONResponse(content={
        "adapted_resume": adapted_resume,
        "match_base": match_base,
        "match_adjusted": match_adjusted
    })


@app.post("/unconfirmed_to_terms")
async def unconfirmed_to_terms_endpoint(request: Request):
    data = await request.json()
    result = unconfirmed2terms(data)
    return JSONResponse(content=result)


@app.post("/buttons")
async def buttons_endpoint(request: Request):
    data = await request.json()
    result = buttons(data)
    return JSONResponse(content=result)


@app.post("/select_to_confirm_list")
async def select_to_confirm_list_endpoint(request: Request):
    data = await request.json()
    result = select_to_confirm_list(data)
    return JSONResponse(content=result)


@app.post("/auto_confirm")
async def auto_confirm_terms_endpoint(request: Request):
    """
    Эндпоинт принимает master_resume и ToConfirm_list,
    возвращает обновленный master_resume.
    """
    data = await request.json()

    master_resume = data.get("master_resume")
    to_confirm_list = data.get("ToConfirm_list")

    if not master_resume or not to_confirm_list:
        return JSONResponse(
            content={"error": "Both master_resume and ToConfirm_list must be provided"},
            status_code=400
        )

    result = auto_confirm_terms(master_resume, {"ToConfirm_list": to_confirm_list})
    return JSONResponse(content=result)


@app.post("/remove_duplicates")
async def remove_unconfirmed_and_unused_endpoint(request: Request):
    """
    Expects JSON:
    {
        "duplicates": ["resume optimization", "stakeholder communication"],
        "master_resume": { ... full JSON ... }
    }
    """
    data = await request.json()
    duplicates = data.get("duplicates", [])
    master_resume = data.get("master_resume", {})

    result = remove_duplicates(duplicates, master_resume)
    return JSONResponse(content=result)

@app.post("/normalize_master")
async def normalize_master_resume_endpoint(request: Request):
    """
    Вебхук для нормализации master_resume.json:
    - восстанавливает unconfirmed.skills и unconfirmed.keywords
    - синхронизирует confirmed_by с буллетами
    """
    try:
        # Получаем JSON из запроса
        data = await request.json()

        # Обрабатываем
        result = normalize_master_resume(data)

        # Возвращаем обновлённый JSON
        return JSONResponse(content=result)

    except Exception as e:
        # На случай ошибок возвращаем их в ответе
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )