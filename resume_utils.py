from fastapi import HTTPException
import json
from collections import Counter
import copy
from itertools import combinations
from math import floor, ceil

def merge_jsons(master_resume, terms):
    err_msg_list = []

    new_bullet_id = max(bullet["id"] for job in master_resume['experience'] for bullet in job["bullets"])

    for term in terms['terms']:
        if term['used']:
            company_found = False
            for experience in master_resume['experience']:
                if experience['company'].lower() == term['company'].lower():
                    company_found = True
                    new_bullet_id = new_bullet_id + 1
                    new_bullet = {
                        'id': new_bullet_id,
                        'text': term['generated_bullet'],
                        'skills_used': [],
                        'keyword_used': []
                    }
                    if term['type'] == 'skill':
                        new_bullet['skills_used'].append(term['term'])
                        for skill in master_resume['skills']['hard_skills']:
                            if skill['term'] == term['term']:
                                skill['confirmed_by'].append(new_bullet_id)
                        for skill in master_resume['skills']['soft_skills']:
                            if skill['term'] == term['term']:
                                skill['confirmed_by'].append(new_bullet_id)
                        if term['term'] in master_resume['unconfirmed']['skills']:
                            master_resume['unconfirmed']['skills'].remove(term['term'])
                    else:
                        new_bullet['keyword_used'].append(term['term'])
                        for keyword in master_resume['keywords']:
                            if keyword['term'] == term['term']:
                                keyword['confirmed_by'].append(new_bullet_id)
                        if term['term'] in master_resume['unconfirmed']['keywords']:
                            master_resume['unconfirmed']['keywords'].remove(term['term'])
                    experience['bullets'].append(new_bullet)
                    break
            if not company_found:
                err_msg_list.append(f"Company '{term['company']}' not found in experience")
                raise HTTPException(status_code=400, detail=err_msg_list)
        else:
            if term['type'] == 'skill':
                master_resume['explicitly_not_used']['skills'].append(term['term'])
                if term['term'] in master_resume['unconfirmed']['skills']:
                    master_resume['unconfirmed']['skills'].remove(term['term'])
                master_resume['skills']['hard_skills'] = [skill for skill in master_resume['skills']['hard_skills'] if skill['term']!= term['term']]
                master_resume['skills']['soft_skills'] = [skill for skill in master_resume['skills']['soft_skills'] if skill['term']!= term['term']]
            else:
                master_resume['explicitly_not_used']['keywords'].append(term['term'])
                if term['term'] in master_resume['unconfirmed']['keywords']:
                    master_resume['unconfirmed']['keywords'].remove(term['term'])
                master_resume['keywords'] = [keyword for keyword in master_resume['keywords'] if keyword['term']!= term['term']]
    
    return master_resume

def format_google_doc_content(input_data):
    """
    Форматирует Google Docs контент, учитывая вложенную структуру.
    Удаляет префиксы и применяет стили в одном batchUpdate запросе.
    Корректно учитывает индексные сдвиги для каждого удаления.
    """
    styles = {
        '[[h1]]': {'fontSize': 17, 'bold': False, 'alignment': 'CENTER', 'list': None},
        '[[h2]]': {'fontSize': 12, 'bold': True,  'alignment': 'START',  'list': None},
        '[[h3]]': {'fontSize': 11, 'bold': False, 'alignment': 'START',  'list': None},
        '[[h4]]': {'fontSize': 8,  'bold': False, 'alignment': 'START',  'list': None},
        '[[b1]]': {'fontSize': 8,  'bold': False, 'alignment': 'START',  'list': 'BULLET_DISC_CIRCLE_SQUARE'},
        '[[b2]]': {'fontSize': 8,  'bold': False, 'alignment': 'START',  'list': None, 'indentFirstLine': {'magnitude': 21.259842519685044, 'unit': 'PT'}, 'indentStart': {'magnitude': 21.259842519685044, 'unit': 'PT'}},
        '[[b3]]': {'fontSize': 8,  'bold': False, 'alignment': 'START',  'list': None, 'indentFirstLine': {'magnitude': 21.259842519685044, 'unit': 'PT'}, 'indentStart': {'magnitude': 21.259842519685044, 'unit': 'PT'}},
        '[[l1]]': {'fontSize': 9,  'bold': False, 'alignment': 'CENTER', 'list': None},
        '[[l2]]': {'fontSize': 8,  'bold': False, 'alignment': 'CENTER', 'list': None}
    }

    content = input_data.get('content', [])

    # Сначала собираем все найденные параграфы с префиксами в список
    found_items = []

    for para in content:
        start = para.get('startIndex', 0)
        end = para.get('endIndex', 0)

        paragraph = para.get('paragraph', {})
        elements = paragraph.get('elements', [])

        # Получаем весь текст параграфа (конкатенация содержимого всех textRun)
        text = ''
        for el in elements:
            textRun = el.get('textRun')
            if textRun and 'content' in textRun:
                text += textRun['content']

        # Ищем какой префикс используется
        for prefix, style in styles.items():
            if text.startswith(prefix):
                prefix_len = len(prefix)
                found_items.append({
                    'start': start,
                    'end': end,
                    'prefix_len': prefix_len,
                    'style': style,
                    'text': text,
                })
                break  # префикс найден — можно дальше не искать

    # Сортируем по start индексу в порядке убывания (чтобы работать с конца документа)
    found_items.sort(key=lambda x: x['start'], reverse=True)

    requests = []

    for item in found_items:
        adjusted_start = item['start']
        adjusted_end = item['end']
        prefix_len = item['prefix_len']
        style = item['style']

        # Проверяем валидность индексов
        if adjusted_start < 0 or adjusted_end <= adjusted_start:
            continue

        # Удаляем префикс
        requests.append({
            'deleteContentRange': {
                'range': {
                    'startIndex': adjusted_start,
                    'endIndex': adjusted_start + prefix_len,
                }
            }
        })

        # Новый диапазон после удаления префикса для форматирования —
        # начинаем с adjusted_start, заканчиваем на adjusted_end - prefix_len
        style_start = adjusted_start
        style_end = adjusted_end - prefix_len

        if style_start >= style_end:
            continue  # после удаления префикса пустой диапазон для стилей

        # Обновляем стиль текста
        requests.append({
            'updateTextStyle': {
                'range': {'startIndex': style_start, 'endIndex': style_end},
                'textStyle': {
                    'bold': style['bold'],
                    'fontSize': {'magnitude': style['fontSize'], 'unit': 'PT'},
                    'weightedFontFamily': {'fontFamily': 'Lexend'}
                },
                'fields': 'bold,fontSize,weightedFontFamily'
            }
        })

        # Обновляем стиль параграфа
        # Формируем paragraphStyle динамически, включая только поля из текущего стиля
        paragraph_style = {'alignment': style['alignment']}

        # Опциональные поля для отступов, которые есть только у некоторых стилей (например у <b2>)
        if 'indentFirstLine' in style:
            paragraph_style['indentFirstLine'] = style['indentFirstLine']
        if 'indentStart' in style:
            paragraph_style['indentStart'] = style['indentStart']

        # Формируем fields из ключей paragraph_style (например 'alignment,indentFirstLine,indentStart')
        fields = ','.join(paragraph_style.keys())

        # Добавляем запрос в requests
        requests.append({
            'updateParagraphStyle': {
                'range': {'startIndex': style_start, 'endIndex': style_end},
                'paragraphStyle': paragraph_style,
                'fields': fields
            }
        })

        # Если нужно — применяем списки
        if style['list'] is not None:
            requests.append({
                'createParagraphBullets': {
                    'range': {'startIndex': style_start, 'endIndex': style_end},
                    'bulletPreset': style['list']
                }
            })

    return {'requests': requests}

def term_in_list(term, items):
    """Helper: check term presence in list of dicts by term (case-insensitive)."""
    term_lower = term.lower()
    for item in items:
        if item["term"].lower() == term_lower:
            return True
    return False


def find_gaps_and_update_master(extract, master_resume):
    """
    Compare extract.required_skills/keywords with master_resume skills/keywords,
    add missing terms to unconfirmed + proper sections (with empty confirmed_by),
    unless present in explicitly_not_used.
    Return updated master_resume.
    """
    # Prepare sets of skill/keyword terms in master which are confirmed
    hard_skills = set(
        s["term"].lower()
        for s in master_resume.get("skills", {}).get("hard_skills", [])
        if s.get("confirmed_by") and len(s["confirmed_by"]) > 0
    )
    soft_skills = set(
        s["term"].lower()
        for s in master_resume.get("skills", {}).get("soft_skills", [])
        if s.get("confirmed_by") and len(s["confirmed_by"]) > 0
    )
    keywords = set(
        k["term"].lower()
        for k in master_resume.get("keywords", [])
        if k.get("confirmed_by") and len(k["confirmed_by"]) > 0
    )

    # Explicitly not used terms
    explicitly_not_used_skills = set(
        s.lower() for s in master_resume.get("explicitly_not_used", {}).get("skills", [])
    )
    explicitly_not_used_keywords = set(
        k.lower() for k in master_resume.get("explicitly_not_used", {}).get("keywords", [])
    )

    # Initialize unconfirmed if absent
    if "unconfirmed" not in master_resume:
        master_resume["unconfirmed"] = {"skills": [], "keywords": []}
    if "skills" not in master_resume["unconfirmed"]:
        master_resume["unconfirmed"]["skills"] = []
    if "keywords" not in master_resume["unconfirmed"]:
        master_resume["unconfirmed"]["keywords"] = []

    # Ensure skills/keywords containers exist
    if "skills" not in master_resume:
        master_resume["skills"] = {"hard_skills": [], "soft_skills": []}
    if "hard_skills" not in master_resume["skills"]:
        master_resume["skills"]["hard_skills"] = []
    if "soft_skills" not in master_resume["skills"]:
        master_resume["skills"]["soft_skills"] = []
    if "keywords" not in master_resume:
        master_resume["keywords"] = []

    # Helper to add to unconfirmed + master section without duplicates
    def add_unconfirmed_skill(term, typ):
        # add to unconfirmed
        if term.lower() not in [s.lower() for s in master_resume["unconfirmed"]["skills"]]:
            master_resume["unconfirmed"]["skills"].append(term)

        # add skeleton to master_resume.skills
        skill_list = master_resume["skills"]["hard_skills"] if typ == "hard" else master_resume["skills"]["soft_skills"]
        if not term_in_list(term, skill_list):
            skill_list.append({"term": term, "confirmed_by": []})

    def add_unconfirmed_keyword(term):
        # add to unconfirmed
        if term.lower() not in [k.lower() for k in master_resume["unconfirmed"]["keywords"]]:
            master_resume["unconfirmed"]["keywords"].append(term)

        # add skeleton to master_resume.keywords
        if not term_in_list(term, master_resume["keywords"]):
            master_resume["keywords"].append({"term": term, "confirmed_by": []})

    # Check skills (hard/soft) with synonyms from extract
    for skill_req in extract.get("required_skills", []):
        term = skill_req["term"]
        synonyms = skill_req.get("synonyms", [])
        typ = skill_req.get("type", "hard")

        candidates = [term] + synonyms
        candidates_lower = [c.lower() for c in candidates]

        # Skip if any candidate is in explicitly_not_used
        if any(c in explicitly_not_used_skills for c in candidates_lower):
            continue

        # Check confirmed sets + raw list in master
        if typ == "hard":
            already_present = any(
                c in hard_skills or term_in_list(c, master_resume["skills"]["hard_skills"])
                for c in candidates
            )
            if not already_present:
                add_unconfirmed_skill(term, "hard")
        elif typ == "soft":
            already_present = any(
                c in soft_skills or term_in_list(c, master_resume["skills"]["soft_skills"])
                for c in candidates
            )
            if not already_present:
                add_unconfirmed_skill(term, "soft")

    # Check keywords with synonyms from extract
    for keyword_req in extract.get("required_keywords", []):
        term = keyword_req["term"]
        synonyms = keyword_req.get("synonyms", [])
        candidates = [term] + synonyms
        candidates_lower = [c.lower() for c in candidates]

        if any(c in explicitly_not_used_keywords for c in candidates_lower):
            continue

        already_present = any(
            c in keywords or term_in_list(c, master_resume["keywords"])
            for c in candidates
        )
        if not already_present:
            add_unconfirmed_keyword(term)

    return master_resume


def match_terms(extract_terms, resume_terms):
    """
    Compute matched terms count using synonyms
    extract_terms: list of dicts with term + synonyms
    resume_terms: list of dicts with term only
    Returns count of matched terms and total terms
    """
    matched_count = 0
    total = len(extract_terms)
    resume_terms_lower = set([r["term"].lower() for r in resume_terms])

    for ext_term in extract_terms:
        candidates = [ext_term["term"].lower()] + [syn.lower() for syn in ext_term.get("synonyms", [])]
        if any(c in resume_terms_lower for c in candidates):
            matched_count += 1
    return matched_count, total


def calculate_match_percent(matched, total):
    if total == 0:
        return 100.0
    return round((matched / total) * 100, 2)


def gather_origin_terms(master_resume):
    """
    Collect all origin terms from master resume (skills hard+soft and keywords) as lists of dicts {term, synonyms=[]}
    For match base calculation.
    """
    origin_skills = []
    for skill_type in ["hard_skills", "soft_skills"]:
        for s in master_resume.get("skills", {}).get(skill_type, []):
            if s.get("origin", False):
                origin_skills.append({"term": s["term"], "synonyms": []})

    origin_keywords = [
        {"term": k["term"], "synonyms": []}
        for k in master_resume.get("keywords", [])
        if k.get("origin", False)
    ]
    return origin_skills, origin_keywords

def gather_all_current_terms(master_resume):
    """
    Gather all current skills and keywords (hard+soft skills and keywords),
    ignoring origin flag, for adjusted match calculation.
    """
    all_skills = []

    for skill_type in ["hard_skills", "soft_skills"]:
        all_skills.extend(
            {"term": s["term"], "synonyms": []}
            for s in master_resume.get("skills", {}).get(skill_type, [])
        )

    all_keywords = [
        {"term": k["term"], "synonyms": []}
        for k in master_resume.get("keywords", [])
    ]

    return all_skills, all_keywords


def _make_serializable(obj):
    """Рекурсивно преобразует объект к JSON-сериализуемому виду:
       - set/tuple -> list
       - dict/list -> рекурсивно
       - прочие типы: оставляет, если сериализуемы, иначе строку repr"""
    if isinstance(obj, dict):
        return {str(k): _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, set):
        return [_make_serializable(v) for v in sorted(obj, key=lambda x: str(x))]
    # примитивы (int/float/str/bool/None)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # fallback: попытка сериализации через json, иначе repr
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return repr(obj)

def debug_log(debug_info, name, obj, as_text=False, head=None, limit=None):
    """
    Безопасно записывает снимок obj в debug_info[name].
    - as_text=True -> сохранит prettified JSON-строку
    - head=N -> если obj — список/iterable, сохранит только первые N элементов
    - limit -> ограничение длины строки при as_text
    """
    try:
        snapshot = copy.deepcopy(obj)
        # если надо взять только head элементов
        if head is not None and isinstance(snapshot, (list, tuple, set)):
            snapshot = list(snapshot)[:head]
        serial = _make_serializable(snapshot)
        if as_text:
            s = json.dumps(serial, ensure_ascii=False, indent=2)
            if limit and len(s) > limit:
                s = s[:limit] + "\n...(truncated)"
            debug_info[name] = s
        else:
            debug_info[name] = serial
    except Exception as e:
        debug_info[name] = f"<debug_log error: {e}>"


def filter_and_rank_bullets(master_resume, extract):
    """
    Адаптирует резюме под вакансию, гарантируя:
    - обязательные термины из экстракта, которые есть в резюме, сохраняются
    - выбираются буллеты для покрытия максимального числа терминов при лимитах per-company и per-bullet
    - optional термины добавляются только после покрытия mandatory
    - структура резюме и связи confirmed_by сохраняются
    """
    
    #debug
    debug_info = {}

    # ---------- 1. Подготовка: map термин → root + приоритет ----------
    term_to_root = {}
    priority_map = {}

    for group in ["required_skills", "required_keywords"]:
        for item in extract.get(group, []):
            root = item["term"]
            priority = item.get("priority", 1000)

            term_to_root[root.lower()] = root
            priority_map[root.lower()] = priority

            for syn in item.get("synonyms", []):
                term_to_root[syn.lower()] = root
                if syn.lower() not in priority_map or priority < priority_map[syn.lower()]:
                    priority_map[syn.lower()] = priority
    
    #debug
    debug_log(debug_info, "terms_to_root_keys", list(term_to_root.keys()))
    debug_log(debug_info, "priority_map", {k: priority_map[k] for k in sorted(priority_map.keys())}, head=100)


    # ---------- 2. Подготовка master_resume ----------
    full_skill_pool = {}
    skill_type_map = {}
    origin_map = {}

    for s in master_resume.get("skills", {}).get("hard_skills", []):
        key = s["term"].lower()
        full_skill_pool[key] = s
        skill_type_map[key] = "hard"
        origin_map[key] = s.get("origin", False)
    for s in master_resume.get("skills", {}).get("soft_skills", []):
        key = s["term"].lower()
        full_skill_pool[key] = s
        skill_type_map[key] = "soft"
        origin_map[key] = s.get("origin", False)
    for k in master_resume.get("keywords", []):
        key = k["term"].lower()
        full_skill_pool[key] = k
        skill_type_map[key] = "keyword"
        origin_map[key] = k.get("origin", False)

    # ---------- 3. Извлечение и нормализация буллетов ----------
    bullets_by_company = {}
    bullet_to_company = {}
    for exp in master_resume.get("experience", []):
        company_id = exp.get("company_id")
        if company_id not in bullets_by_company:
            bullets_by_company[company_id] = []
        for b in exp.get("bullets", []):
            bullet_copy = copy.deepcopy(b)
            bullet_copy["skills_used"] = [term_to_root.get(t.lower(), t) for t in b.get("skills_used", [])]
            bullet_copy["keyword_used"] = [term_to_root.get(k.lower(), k) for k in b.get("keyword_used", [])]
            bullets_by_company[company_id].append(bullet_copy)
            bullet_to_company[bullet_copy["id"]] = company_id

    #debug
    #записываем только идентификаторы буллетов и их terms (без вложенных несериализуемых типов)
    safe_bullets_by_company = {cid: [{ "id": b.get("id"), "terms": list(b.get("skills_used",[])+b.get("keyword_used",[])) } 
                                    for b in bullets]
                               for cid, bullets in bullets_by_company.items()}
    debug_log(debug_info, "bullets_by_company_safe", safe_bullets_by_company, as_text=True, limit=2000)
    debug_log(debug_info, "bullet_to_company", bullet_to_company)

    # ---------- 4. Определяем множества терминов ----------
    mandatory_terms = set(
        term_to_root.get(t.lower(), t) 
        for t in (extract.get("mandatory", {}).get("skills", []) + extract.get("mandatory", {}).get("keywords", []))
    )
    nice_terms = set(
        term_to_root.get(t.lower(), t) 
        for t in (extract.get("nice_to_have", {}).get("skills", []) + extract.get("nice_to_have", {}).get("keywords", []))
    )
    # термины реально встречающиеся в резюме
    resume_terms = set(t.lower() for t in full_skill_pool.keys())
    mandatory_terms = set(t for t in mandatory_terms if t.lower() in resume_terms)
    nice_terms = set(t for t in nice_terms if t.lower() in resume_terms)

    #debug
    debug_log(debug_info, "resume_terms", sorted(list(resume_terms)))
    debug_log(debug_info, "mandatory_terms", sorted(list(mandatory_terms)))
    debug_log(debug_info, "nice_terms", sorted(list(nice_terms)))

    # ---------- 5. Комбинаторный выбор буллетов per company ----------
    MAX_TERMS = 25
    MAX_TERMS_PER_BULLET = 3

    selected_terms = set()
    selected_bullets = []

    # Сначала гарантируем покрытие всех mandatory
    for company_id, bullets in bullets_by_company.items():
        # лимит по компании
        duration_years = next((exp.get("duration_years", 0) for exp in master_resume.get("experience", []) if exp.get("company_id")==company_id), 0)
        company_cap = ceil(duration_years) + 1 if duration_years - floor(duration_years) >= 0.5 else floor(duration_years) + 1

        bullet_term_map = []
        for b in bullets:
            all_terms = b.get("skills_used", []) + b.get("keyword_used", [])
            bullet_term_map.append((b, set(all_terms)))

        #debug
        safe_btm = [ {"id": b.get("id"), "terms": sorted(list(terms))} for b, terms in bullet_term_map ]
        debug_log(debug_info, f"company_{company_id}_bullet_term_map_safe", safe_btm, head=200)

        # Подбираем комбинации для полного покрытия mandatory
        mandatory_in_company = mandatory_terms.copy()
        best_combo = []
        best_coverage = set()
        for r in range(1, min(company_cap, len(bullet_term_map)) + 1):
            for combo in combinations(bullet_term_map, r):
                combo_terms = set()
                for b, _ in combo:
                    combo_terms.update(b.get("skills_used", []) + b.get("keyword_used", []))
                coverage_terms = combo_terms & mandatory_in_company
                if len(coverage_terms) > len(best_coverage):
                    best_combo = [b for b,_ in combo]
                    best_coverage = coverage_terms
                elif len(coverage_terms) == len(best_coverage) and len(combo) < len(best_combo):
                    best_combo = [b for b,_ in combo]
        selected_bullets.extend(best_combo)
        selected_terms.update(best_coverage)
    
    #debug
    debug_log(debug_info, "5.selected_bullets_ids", [b.get("id") for b in selected_bullets])
    debug_log(debug_info, "5.selected_terms", sorted(list(selected_terms)))

    # ---------- 6. Добавляем nice_to_have термины ----------
    for company_id, bullets in bullets_by_company.items():
        bullet_term_map = [(b, set(b.get("skills_used", []) + b.get("keyword_used", []))) for b in bullets]
        for b, terms in bullet_term_map:
            if b in selected_bullets:
                continue
            if len(selected_bullets) >= company_cap:
                break
            # добавляем буллет, если содержит новый nice_to_have термин
            new_terms = terms & nice_terms - selected_terms
            if new_terms:
                selected_bullets.append(b)
                selected_terms.update(new_terms)

    #debug
    debug_log(debug_info, "6.selected_bullets_ids", [b.get("id") for b in selected_bullets])
    debug_log(debug_info, "6.selected_terms", sorted(list(selected_terms)))

    # ---------- 7. Добавляем optional термины до лимита ----------
    optional_terms = [t for t in resume_terms if t not in selected_terms]
    for term in sorted(optional_terms, key=lambda t: priority_map.get(t.lower(), 1000)):
        if len(selected_terms) < MAX_TERMS:
            selected_terms.add(term)
        else:
            break

    #debug
    debug_log(debug_info, "7.optional_terms", sorted(list(optional_terms))[:200], as_text=True)
    debug_log(debug_info, "7.selected_terms_and_optional", sorted(list(selected_terms)))

    # ---------- 8. Обрезка терминов в буллетах до MAX_TERMS_PER_BULLET ----------
    final_bullets = []
    for b in selected_bullets:
        all_terms = b.get("skills_used", []) + b.get("keyword_used", [])
        filtered_terms = [t for t in all_terms if t in selected_terms][:MAX_TERMS_PER_BULLET]
        if filtered_terms:
            b["skills_used"] = [t for t in filtered_terms if skill_type_map.get(t.lower()) in ["hard","soft"]]
            b["keyword_used"] = [t for t in filtered_terms if skill_type_map.get(t.lower()) == "keyword"]
            final_bullets.append(b)

    #debug
    debug_log(debug_info, "8.final_bullets_safe", [{ "id": b.get("id"), "terms": list(b.get("skills_used", []) + b.get("keyword_used", [])) } for b in final_bullets], as_text=True, limit=2000)

    # ---------- 9. Сортировка буллетов внутри компаний по важности ----------
    bullets_by_company_final = {}
    for b in final_bullets:
        company_id = bullet_to_company.get(b["id"])
        if company_id is not None:
            bullets_by_company_final.setdefault(company_id, []).append(b)
    for bullets in bullets_by_company_final.values():
        bullets.sort(key=lambda b: min([priority_map.get(t.lower(), 1000) for t in b.get("skills_used", []) + b.get("keyword_used", [])]) if (b.get("skills_used", []) + b.get("keyword_used", [])) else 1000)

    #debug
    debug_log(debug_info, "9.bullets_by_company_final_safe", {cid: [{ "id": bb.get("id"), "terms": list(bb.get("skills_used", []) + bb.get("keyword_used", [])) } for bb in blist] for cid, blist in bullets_by_company_final.items()}, as_text=True, limit=2000)

    # ---------- 10. Формируем адаптированные skills и keywords ----------
    adapted_hard = {}
    adapted_soft = {}
    adapted_keywords = {}
    for b in final_bullets:
        b_id = b["id"]
        for t in b.get("skills_used", []) + b.get("keyword_used", []):
            term_l = t.lower()
            root = term_to_root.get(term_l, t)
            origin_flag = origin_map.get(term_l, False)
            skill_type = skill_type_map.get(term_l)

            target_dict = None
            if skill_type == "hard":
                target_dict = adapted_hard
            elif skill_type == "soft":
                target_dict = adapted_soft
            elif skill_type == "keyword":
                target_dict = adapted_keywords

            if target_dict is None:
                continue

            if root not in target_dict:
                target_dict[root] = {"term": root, "confirmed_by": [], "origin": origin_flag}
            if b_id not in target_dict[root]["confirmed_by"]:
                target_dict[root]["confirmed_by"].append(b_id)

    # ---------- 11. Восстанавливаем experience ----------
    bullet_map = {b["id"]: b for b in final_bullets}
    restored_experience = []
    for company in copy.deepcopy(master_resume.get("experience", [])):
        original_ids = [b.get("id") for b in company.get("bullets", []) if "id" in b]
        filtered_bullets = [bullet_map[bid] for bid in original_ids if bid in bullet_map]
        if filtered_bullets:
            filtered_bullets.sort(key=lambda b: min([priority_map.get(t.lower(), 1000) for t in b.get("skills_used", []) + b.get("keyword_used", [])]) if (b.get("skills_used", []) + b.get("keyword_used", [])) else 1000)
            company["bullets"] = filtered_bullets
            restored_experience.append(company)

    # ---------- 12. Формируем итог ----------
    adapted_resume = copy.deepcopy(master_resume)
    adapted_resume["skills"] = {
        "hard_skills": list(adapted_hard.values()),
        "soft_skills": list(adapted_soft.values())
    }
    adapted_resume["keywords"] = list(adapted_keywords.values())
    adapted_resume["experience"] = restored_experience

    if "job_title" in extract:
        adapted_resume["desired_positions"] = [extract["job_title"]]

    #debug
    adapted_resume["_debug_info"] = debug_info

    return adapted_resume


def unconfirmed2terms(input_data):
    unconfirmed = input_data.get("unconfirmed", {})

    terms = []
    skills = unconfirmed.get("skills", [])
    keywords = unconfirmed.get("keywords", [])

    for skill in skills:
        terms.append({
            "term": skill,
            "type": "skill",
            "used": True,
            "answer_raw": "",
            "generated_bullet": "",
            "company": ""
        })
    for keyword in keywords:
        terms.append({
            "term": keyword,
            "type": "keyword",
            "used": True,
            "answer_raw": "",
            "generated_bullet": "",
            "company": ""
        })
    return {"terms": terms}

def buttons(data: dict) -> dict:
    companies = data.get("companies", [])
    
    inline_keyboard = [[{"text": c, "callback_data": c}] for c in companies]
    
    return {
        "inline_keyboard": inline_keyboard
    }

def select_to_confirm_list(master_resume: dict) -> dict:
    """
    Собирает два списка из master_resume.json:
    1. ToConfirm_list: skills/keywords без confirmed_by
    2. Bullets: только id и text из experience[].bullets
    """

    to_confirm = []

    # Hard skills
    for skill in master_resume.get("skills", {}).get("hard_skills", []):
        if not skill.get("confirmed_by"):
            to_confirm.append({
                "term": skill["term"],
                "type": "skill:hard"
            })

    # Soft skills
    for skill in master_resume.get("skills", {}).get("soft_skills", []):
        if not skill.get("confirmed_by"):
            to_confirm.append({
                "term": skill["term"],
                "type": "skill:soft"
            })

    # Keywords
    for kw in master_resume.get("keywords", []):
        if not kw.get("confirmed_by"):
            to_confirm.append({
                "term": kw["term"],
                "type": "keyword"
            })

    # Собираем список bullets
    bullets = []
    for exp in master_resume.get("experience", []):
        for bullet in exp.get("bullets", []):
            bullets.append({
                "id": bullet["id"],
                "text": bullet["text"]
            })

    return {
        "ToConfirm_list": to_confirm,
        "Bullets": bullets
    }

def auto_confirm_terms(master_resume: dict, to_confirm_list: dict) -> dict:
    """
    Обновляет master_resume.json на основании ToConfirm_list с confirmed_by.

    Шаги:
    1. Проставляет confirmed_by в skills/keywords
    2. Обновляет bullets[].skills_used / bullets[].keyword_used
    3. Удаляет подтверждённые термины из unconfirmed
    """

    # Удобный индекс для буллетов
    bullet_map = {
        bullet["id"]: bullet
        for exp in master_resume.get("experience", [])
        for bullet in exp.get("bullets", [])
    }

    # Пройдемся по каждому элементу ToConfirm_list
    for item in to_confirm_list.get("ToConfirm_list", []):
        term = item["term"]
        term_type = item["type"]
        confirmed_by = item.get("confirmed_by", [])

        # Обновляем confirmed_by в секции master_resume
        target_section = None

        if term_type.startswith("skill"):
            # Проверяем сначала hard, потом soft
            for skill in master_resume.get("skills", {}).get("hard_skills", []):
                if skill["term"] == term:
                    skill["confirmed_by"] = confirmed_by
                    target_section = "skill"
                    break
            for skill in master_resume.get("skills", {}).get("soft_skills", []):
                if skill["term"] == term:
                    skill["confirmed_by"] = confirmed_by
                    target_section = "skill"
                    break

        elif term_type == "keyword":
            for kw in master_resume.get("keywords", []):
                if kw["term"] == term:
                    kw["confirmed_by"] = confirmed_by
                    target_section = "keyword"
                    break

        # Если confirmed_by не пустой → вставляем в bullets
        if confirmed_by:
            for bullet_id in confirmed_by:
                bullet = bullet_map.get(bullet_id)
                if not bullet:
                    continue

                if target_section == "skill":
                    if "skills_used" not in bullet:
                        bullet["skills_used"] = []
                    if term not in bullet["skills_used"]:
                        bullet["skills_used"].append(term)

                elif target_section == "keyword":
                    if "keyword_used" not in bullet:
                        bullet["keyword_used"] = []
                    if term not in bullet["keyword_used"]:
                        bullet["keyword_used"].append(term)

    # Удаляем подтверждённые термины из unconfirmed
    confirmed_terms = {
        item["term"] for item in to_confirm_list.get("ToConfirm_list", [])
        if item.get("confirmed_by")
    }

    master_resume["unconfirmed"]["skills"] = [
        s for s in master_resume.get("unconfirmed", {}).get("skills", [])
        if s not in confirmed_terms
    ]
    master_resume["unconfirmed"]["keywords"] = [
        k for k in master_resume.get("unconfirmed", {}).get("keywords", [])
        if k not in confirmed_terms
    ]

    return master_resume


def remove_unconfirmed_and_unused_terms(duplicates: list[str], master_resume: dict) -> dict:
    """
    Removes duplicate terms from:
      - unconfirmed.skills
      - unconfirmed.keywords
      - skills.hard_skills (if confirmed_by is empty)
      - skills.soft_skills (if confirmed_by is empty)
      - keywords (if confirmed_by is empty)
    """
    # Подготовка множества дубликатов в нижнем регистре
    duplicates_lower = {d.lower() for d in duplicates}

    cleaned = master_resume.copy()

    # Очистка unconfirmed.skills (список строк)
    if "unconfirmed" in cleaned and "skills" in cleaned["unconfirmed"]:
        cleaned["unconfirmed"]["skills"] = [
            skill for skill in cleaned["unconfirmed"]["skills"]
            if skill.lower() not in duplicates_lower
        ]

    # Очистка unconfirmed.keywords (список строк)
    if "unconfirmed" in cleaned and "keywords" in cleaned["unconfirmed"]:
        cleaned["unconfirmed"]["keywords"] = [
            kw for kw in cleaned["unconfirmed"]["keywords"]
            if kw.lower() not in duplicates_lower
        ]

    # Очистка skills.hard_skills
    if "skills" in cleaned and "hard_skills" in cleaned["skills"]:
        cleaned["skills"]["hard_skills"] = [
            obj for obj in cleaned["skills"]["hard_skills"]
            if not (obj.get("term", "").lower() in duplicates_lower and obj.get("confirmed_by") == [])
        ]

    # Очистка skills.soft_skills
    if "skills" in cleaned and "soft_skills" in cleaned["skills"]:
        cleaned["skills"]["soft_skills"] = [
            obj for obj in cleaned["skills"]["soft_skills"]
            if not (obj.get("term", "").lower() in duplicates_lower and obj.get("confirmed_by") == [])
        ]

    # Очистка keywords
    if "keywords" in cleaned:
        cleaned["keywords"] = [
            obj for obj in cleaned["keywords"]
            if not (obj.get("term", "").lower() in duplicates_lower and obj.get("confirmed_by") == [])
        ]

    return cleaned

def normalize_master_resume(master_resume: dict) -> dict:
    """
    Приводит master_resume.json в консистентное состояние:
    0. Дополняет ссылки https:// если нужно. Чтобы в пдф они были кликабельными
    1. Восстанавливает секцию unconfirmed.skills и unconfirmed.keywords
       на основе hard_skills, soft_skills и keywords с пустым confirmed_by.
    2. Гарантирует, что все skills/keywords с confirmed_by действительно
       перечислены в соответствующих буллетах. И подчищает мусорные=несуществующие ID
    3. Проверяет обратную связь — что все термины, перечисленные в буллетах,
       упомянуты в confirmed_by в секции skills/keywords.
    """
    # --- Шаг 0: подготовка удобных ссылок ---
    hard_skills = master_resume.get("skills", {}).get("hard_skills", [])
    soft_skills = master_resume.get("skills", {}).get("soft_skills", [])
    keywords = master_resume.get("keywords", [])
    experience = master_resume.get("experience", [])
    unconfirmed = master_resume.get("unconfirmed", {})
    unconfirmed_skills = set(unconfirmed.get("skills", []))
    unconfirmed_keywords = set(unconfirmed.get("keywords", []))

    # --- Шаг 0.1: дополнение ссылок ---
    def add_https_if_missing(url: str) -> str:
        url = url.strip()
        if url and not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        return url

    l_linkedin = add_https_if_missing(master_resume.get("personal_info", {}).get("linkedin", ""))
    l_portfolio = add_https_if_missing(master_resume.get("personal_info", {}).get("portfolio", ""))

    if "personal_info" not in master_resume:
        master_resume["personal_info"] = {}

    master_resume["personal_info"]["linkedin"] = l_linkedin
    master_resume["personal_info"]["portfolio"] = l_portfolio

    # --- Шаг 1: восстановление unconfirmed ---
    for skill in hard_skills:
        if not skill.get("confirmed_by"):
            unconfirmed_skills.add(skill["term"])

    for skill in soft_skills:
        if not skill.get("confirmed_by"):
            unconfirmed_skills.add(skill["term"])

    for kw in keywords:
        if not kw.get("confirmed_by"):
            unconfirmed_keywords.add(kw["term"])

    master_resume["unconfirmed"] = {
        "skills": sorted(unconfirmed_skills),
        "keywords": sorted(unconfirmed_keywords)
    }

    # --- Шаг 2: синхронизация confirmed_by с буллетами ---
    bullet_index = {}
    for exp in experience:
        for bullet in exp.get("bullets", []):
            bullet_index[bullet["id"]] = bullet

    def ensure_in_list(lst: list, term: str):
        if term not in lst:
            lst.append(term)

    for skill in hard_skills:
        term = skill["term"]
        confirmed_ids = skill.get("confirmed_by", [])
        valid_ids = []
        for bullet_id in confirmed_ids:
            bullet = bullet_index.get(bullet_id)
            if bullet is not None:
                ensure_in_list(bullet.setdefault("skills_used", []), term)
                valid_ids.append(bullet_id)
            # else: пропускаем — тем самым выбрасываем битый ID
        skill["confirmed_by"] = valid_ids

    for skill in soft_skills:
        term = skill["term"]
        confirmed_ids = skill.get("confirmed_by", [])
        valid_ids = []
        for bullet_id in confirmed_ids:
            bullet = bullet_index.get(bullet_id)
            if bullet is not None:
                ensure_in_list(bullet.setdefault("skills_used", []), term)
                valid_ids.append(bullet_id)
        skill["confirmed_by"] = valid_ids

    for kw in keywords:
        term = kw["term"]
        confirmed_ids = kw.get("confirmed_by", [])
        valid_ids = []
        for bullet_id in confirmed_ids:
            bullet = bullet_index.get(bullet_id)
            if bullet is not None:
                ensure_in_list(bullet.setdefault("keyword_used", []), term)
                valid_ids.append(bullet_id)
        kw["confirmed_by"] = valid_ids

    # --- Шаг 3: обратная проверка — добавляем missing confirmed_by ---
    # Создаём быстрый поиск термина в соответствующих секциях
    hard_skill_map = {s["term"]: s for s in hard_skills}
    soft_skill_map = {s["term"]: s for s in soft_skills}
    keyword_map = {k["term"]: k for k in keywords}

    for bullet_id, bullet in bullet_index.items():
        for term in bullet.get("skills_used", []):
            # hard skills
            if term in hard_skill_map:
                ensure_in_list(hard_skill_map[term].setdefault("confirmed_by", []), bullet_id)
            # soft skills
            elif term in soft_skill_map:
                ensure_in_list(soft_skill_map[term].setdefault("confirmed_by", []), bullet_id)

        for term in bullet.get("keyword_used", []):
            if term in keyword_map:
                ensure_in_list(keyword_map[term].setdefault("confirmed_by", []), bullet_id)

    return master_resume

def cv2text(master_resume: dict) -> str:
    """
    Формирует текстовую версию резюме с разметкой вида [[h1]], [[b1]] и т.п.
    для последующего преобразования в формат документа.
    Если end_date пустой — подставляется 'now'.
    """

    #Скелет примерно такой. Были теги <h1> - но гуглдок съедает разметку пришлось делать неузнаваемую [[h1]]   
    #[[h1]]FullName
    #[[l1]]DesiredPosition
    #[[l2]]Location|email|LinkedIn|Portfolio
    #[[h2]]Key skills & Competencies
    #[[b3]]HardSkills
    #[[b2]]Language1 (Proficiency1), Language2 (Proficiency2)
    #[[h2]]Work history
    #[[h3]]Company _name - Job title
    #[[h4]]Jan 2020 - May 2021 | London
    #[[b1]]Bullet text
    #[[h2]]Education
    #[[b2]]Degree - Institution
    #[[b2]]Certification_name

    # --- Блок Personal Info ---
    personal_info = master_resume.get("personal_info", {})
    full_name = personal_info.get("full_name", "").strip()
    email = personal_info.get("email", "").strip()
    location = personal_info.get("location", "").strip()
    linkedin = personal_info.get("linkedin", "").strip()
    portfolio = personal_info.get("portfolio", "").strip()

    desired_positions = master_resume.get("desired_positions", [])
    desired_position = desired_positions[0] if desired_positions else ""

    contact_parts = [p for p in [location, email, linkedin, portfolio] if p]
    contact_line = " | ".join(contact_parts)

    lines = []

    # Заголовок
    if full_name:
        lines.append(f"[[h1]]{full_name}")
    if desired_position:
        lines.append(f"[[l1]]{desired_position}")
    if contact_line:
        lines.append(f"[[l2]]{contact_line}")

    # --- Блок Key Skills & Competencies ---
    hard_skills = master_resume.get("skills", {}).get("hard_skills", [])
    soft_skills = master_resume.get("skills", {}).get("soft_skills", [])
    keywords = master_resume.get("keywords", [])
    languages = master_resume.get("languages", [])

    all_skill_terms = [s["term"] for s in (hard_skills + soft_skills + keywords) if s.get("term")]
    if all_skill_terms or languages:
        lines.append(f"\n[[h2]]Key skills & Competencies")

    if all_skill_terms:
        lines.append(f"[[b3]]{', '.join(all_skill_terms)}")

    if languages:
        lang_parts = []
        for lang in languages:
            language = lang.get("language", "").strip()
            proficiency = lang.get("proficiency", "").strip()
            if not language:
                continue
            if proficiency:
                lang_parts.append(f"{language} ({proficiency})")
            else:
                lang_parts.append(f"{language}")
        if lang_parts:  # ничего не писать, если языков реально нет
            lines.append(f"[[b2]]{', '.join(lang_parts)}")

    # --- Блок Work History ---
    experience = master_resume.get("experience", [])
    if experience:
        lines.append(f"\n[[h2]]Work history")

    for exp in experience:
        company = exp.get("company", "").strip()
        job_title = exp.get("job_title", "").strip()
        location = exp.get("location", "").strip()
        start_date = exp.get("start_date", "").strip()
        end_date = exp.get("end_date", "").strip() or "now"
        bullets = exp.get("bullets", [])

        # Заголовок компании и должности
        if company and job_title:
            lines.append(f"[[h3]]{company} - {job_title}")
        elif company:
            lines.append(f"[[h3]]{company}")
        elif job_title:
            lines.append(f"[[h3]]{job_title}")

        # Даты и локация
        if start_date:  # добавляем проверку, что стартовая дата есть
            date_part = f"{start_date} - {end_date}".strip()
            if location:
                timeline = f"{date_part} | {location}"
            else:
                timeline = date_part

            if timeline:
                lines.append(f"[[h4]]{timeline}")

        # Буллеты
        for bullet in bullets:
            text = bullet.get("text", "").strip()
            if text:
                lines.append(f"[[b1]]{text}")

        # Пустая строка после каждой компании (для читаемости)
        lines.append("")

    # --- Блок Education ---
    education = master_resume.get("education", [])
    certifications = master_resume.get("certifications", [])

    if education or certifications:
        lines.append(f"[[h2]]Education")        #Убрал переход на новую строку, так как остался переход после посл. компании

    for edu in education:
        degree = edu.get("degree", "").strip()
        institution = edu.get("institution", "").strip()
        if degree or institution:
            if degree and institution:
                lines.append(f"[[b2]]{degree} - {institution}")
            else:
                lines.append(f"[[b2]]{degree or institution}")

    for cert in certifications:
        name = cert.get("name", "").strip()
        if name:
            lines.append(f"[[b2]]{name}")

    return "\n".join(lines)


def extract_bullets(input_json: dict) -> str:
    """
    Возвращает строку JSON-массива с буллетами.
    Если skill или keyword повторяется второй раз или позже, 
    он добавляется в общий список "needs_synonyms".
    Формат плоский: skills, keywords, needs_synonyms на одном уровне.
    """
    bullets = []
    skill_counts = Counter()
    keyword_counts = Counter()

    for exp in input_json.get("experience", []):
        for bullet in exp.get("bullets", []):
            skills = bullet.get("skills_used", [])
            keywords = bullet.get("keyword_used", [])

            needs_synonyms = []

            # Проверяем повторы skills
            for s in skills:
                skill_counts[s] += 1
                if skill_counts[s] > 1:
                    needs_synonyms.append(s)

            # Проверяем повторы keywords
            for k in keywords:
                keyword_counts[k] += 1
                if keyword_counts[k] > 1:
                    needs_synonyms.append(k)

            bullet_data = {
                "id": bullet.get("id"),
                "text": bullet.get("text"),
                "skills": skills,
                "keywords": keywords
            }

            if needs_synonyms:
                bullet_data["needs_synonyms"] = needs_synonyms

            bullets.append(bullet_data)

    return json.dumps(bullets, indent=2, ensure_ascii=False)


def push_bullets(data: dict) -> dict:
    """
    Обновляет тексты буллетов в master_resume по id.
    Возвращает обновлённый master_resume.
    """
    bullets_update = {b["id"]: b["text"] for b in data.get("bullets", [])}
    master_resume = data.get("master_resume", {})

    for exp in master_resume.get("experience", []):
        for bullet in exp.get("bullets", []):
            bullet_id = bullet.get("id")
            if bullet_id in bullets_update:
                bullet["text"] = bullets_update[bullet_id]

    return master_resume


def simplify_extract(extract: dict) -> str:
    """
    Возвращает упрощённую структуру job_requirements как JSON-строку.
    """
    simplified = {
        "mandatory": {
            "skills": extract.get("mandatory", {}).get("skills", []),
            "keywords": extract.get("mandatory", {}).get("keywords", [])
        },
        "nice_to_have": {
            "skills": extract.get("nice_to_have", {}).get("skills", []),
            "keywords": extract.get("nice_to_have", {}).get("keywords", [])
        }
    }
    # Красиво форматированный JSON, чтобы легче читать при отладке
    return json.dumps(simplified, ensure_ascii=False, indent=2)
