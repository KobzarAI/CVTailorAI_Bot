from fastapi import HTTPException

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
        '<h1>': {'fontSize': 16, 'bold': True,  'alignment': 'START', 'list': None},
        '<h2>': {'fontSize': 12, 'bold': True,  'alignment': 'START', 'list': None},
        '<h3>': {'fontSize': 11, 'bold': False, 'alignment': 'START', 'list': None},
        '<h4>': {'fontSize': 8,  'bold': False, 'alignment': 'START', 'list': None},
        '<b1>': {'fontSize': 8,  'bold': False, 'alignment': 'START', 'list': 'BULLET_DISC_CIRCLE_SQUARE'},
        '<b2>': {'fontSize': 8,  'bold': False, 'alignment': 'START', 'list': None, 'indentFirstLine': {'magnitude': 21.259842519685044, 'unit': 'PT'}, 'indentStart': {'magnitude': 21.259842519685044, 'unit': 'PT'}}
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
    """Helper: check term presence in list of dicts by term and synonyms (case-insensitive)."""
    term_lower = term.lower()
    for item in items:
        if item["term"].lower() == term_lower:
            return True
        for syn in item.get("synonyms", []):
            if syn.lower() == term_lower:
                return True
    return False


def find_gaps_and_update_master(extract, master_resume):
    """
    Compare extract.required_skills/keywords with master_resume skills/keywords (origin=True),
    add missing terms to unconfirmed section of master_resume.
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
        if s.get("confirmed_by") and len(s["confirmed_by"]) > 0
    )

    # Initialize unconfirmed if absent
    if "unconfirmed" not in master_resume:
        master_resume["unconfirmed"] = {"skills": [], "keywords": []}
    if "skills" not in master_resume["unconfirmed"]:
        master_resume["unconfirmed"]["skills"] = []
    if "keywords" not in master_resume["unconfirmed"]:
        master_resume["unconfirmed"]["keywords"] = []

    # Helper to add to unconfirmed without duplicates
    def add_unconfirmed_skill(term):
        if term.lower() not in [s.lower() for s in master_resume["unconfirmed"]["skills"]]:
            master_resume["unconfirmed"]["skills"].append(term)

    def add_unconfirmed_keyword(term):
        if term.lower() not in [k.lower() for k in master_resume["unconfirmed"]["keywords"]]:
            master_resume["unconfirmed"]["keywords"].append(term)

    # Check skills (hard)
    for skill_req in extract.get("required_skills", []):
        term = skill_req["term"]
        typ = skill_req.get("type", "hard")
        term_lower = term.lower()
        # Determine if in master
        if typ == "hard":
            if term_lower not in hard_skills and not term_in_list(term, master_resume.get("skills", {}).get("hard_skills", [])):
                add_unconfirmed_skill(term)
        elif typ == "soft":
            if term_lower not in soft_skills and not term_in_list(term, master_resume.get("skills", {}).get("soft_skills", [])):
                add_unconfirmed_skill(term)

    # Check keywords
    for keyword_req in extract.get("required_keywords", []):
        term = keyword_req["term"]
        term_lower = term.lower()
        if term_lower not in keywords and not term_in_list(term, master_resume.get("keywords", [])):
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


def filter_and_rank_bullets(master_resume, extract):
    """
    Фильтрует пункты опыта по навыкам/ключевым словам из extract, учитывая синонимы.
    Оставляет в experience у каждой компании количество буллетов = round(duration_years) (+1 при наличии релевантных)
    Сортирует релевантные буллеты по приоритету из extract (чем меньше, тем лучше).
    Если релевантных меньше лимита, добирает из нерелевантных по их изначальному порядку.
    Возвращает обновленное адаптированное резюме.
    """
    # Собираем множество всех релевантных терминов (термин + синонимы, в нижнем регистре)
    relevant_terms = set()
    priority_map = {}  # map term -> priority из extract
    for group in ["required_skills", "required_keywords"]:
        for item in extract.get(group, []):
            term_lower = item["term"].lower()
            relevant_terms.add(term_lower)
            priority_map[term_lower] = item.get("priority", 1000)
            for syn in item.get("synonyms", []):
                syn_lower = syn.lower()
                relevant_terms.add(syn_lower)
                if syn_lower not in priority_map or item.get("priority", 1000) < priority_map.get(syn_lower, 1000):
                    priority_map[syn_lower] = item.get("priority", 1000)

    adapted_experience = []
    for exp in master_resume.get("experience", []):
        duration_years = exp.get("duration_years", 0)
        limit = round(duration_years)  # округляем математически
        # Собираем буллеты с оценкой релевантности и порядком
        bullets_with_meta = []
        for idx, bullet in enumerate(exp.get("bullets", [])):
            bullet_skills = [s.lower() for s in bullet.get("skills_used", [])]
            bullet_keywords = [k.lower() for k in bullet.get("keyword_used", [])]
            bullet_terms = bullet_skills + bullet_keywords
            # Определяем релевантность и min приоритет среди терминов буллета
            bullet_relevant_terms = [t for t in bullet_terms if t in relevant_terms]
            if bullet_relevant_terms:
                bullet_priority = min(priority_map[t] for t in bullet_relevant_terms)
            else:
                bullet_priority = None  # нерелевантный

            bullets_with_meta.append({
                "bullet": bullet,
                "priority": bullet_priority,
                "original_idx": idx
            })
        # Отделяем релевантные от нерелевантных
        relevant_bullets = [b for b in bullets_with_meta if b["priority"] is not None]
        non_relevant_bullets = [b for b in bullets_with_meta if b["priority"] is None]

        # Сортируем релевантные по priority (меньше=выше)
        relevant_bullets.sort(key=lambda x: x["priority"])

        # Лимит +1 буллет если есть дополнительные релевантные буллеты сверх лимита
        extra_bullet = 1 if len(relevant_bullets) > limit else 0
        max_bullets = limit + extra_bullet

        # Отбираем релевантные до лимита (или лимит+1)
        selected_bullets = relevant_bullets[:max_bullets]

        # Если релевантных меньше лимита, добираем нерелевантных по исходному порядку
        if len(selected_bullets) < limit:
            needed = limit - len(selected_bullets)
            # Необходимо отобрать 'needed' первых нерелевантных по original_idx
            non_relevant_bullets.sort(key=lambda x: x["original_idx"])
            selected_bullets.extend(non_relevant_bullets[:needed])

        # В итог записываем только буллеты, извлекая из структуры
        filtered_bullets = [b["bullet"] for b in selected_bullets]

        if filtered_bullets:
            exp_copy = exp.copy()
            exp_copy["bullets"] = filtered_bullets
            adapted_experience.append(exp_copy)

    # Сортировка и фильтрация skills и keywords по приоритету из extract (как было)
    def sort_terms_by_priority(terms, extract_list):
        extract_priority_map = {item["term"].lower(): item.get("priority", 1000) for item in extract_list}
        filtered = [t for t in terms if t["term"].lower() in extract_priority_map]
        return sorted(filtered, key=lambda t: extract_priority_map[t["term"].lower()])

    adapted_hard_skills = sort_terms_by_priority(
        master_resume.get("skills", {}).get("hard_skills", []), extract.get("required_skills", [])
    )
    adapted_soft_skills = sort_terms_by_priority(
        master_resume.get("skills", {}).get("soft_skills", []), extract.get("required_skills", [])
    )
    adapted_keywords = sort_terms_by_priority(
        master_resume.get("keywords", []), extract.get("required_keywords", [])
    )

    # Создаём копию мастер резюме и заменяем там адаптированные секции
    adapted_master = master_resume.copy()
    adapted_master["experience"] = adapted_experience
    adapted_master["skills"]["hard_skills"] = adapted_hard_skills
    adapted_master["skills"]["soft_skills"] = adapted_soft_skills
    adapted_master["keywords"] = adapted_keywords

    return adapted_master


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