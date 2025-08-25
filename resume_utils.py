def merge_jsons(master_resume, terms):
    err_msg_list = []

    new_bullet_id = max(bullet["id"] for job in master_resume['experience'] for bullet in job["bullets"])

    for term in terms['terms']:
        if term['used']:
            company_found = False
            for experience in master_resume['experience']:
                if experience['company'] == term['company']:
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
    # Prepare sets of origin skill/keyword terms in master
    origin_hard_skills = set(
        s["term"].lower()
        for s in master_resume.get("skills", {}).get("hard_skills", [])
        if s.get("origin", False)
    )
    origin_soft_skills = set(
        s["term"].lower()
        for s in master_resume.get("skills", {}).get("soft_skills", [])
        if s.get("origin", False)
    )
    origin_keywords = set(
        k["term"].lower()
        for k in master_resume.get("keywords", [])
        if k.get("origin", False)
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
        # Determine if in origin master
        if typ == "hard":
            if term_lower not in origin_hard_skills and not term_in_list(term, master_resume.get("skills", {}).get("hard_skills", [])):
                add_unconfirmed_skill(term)
        elif typ == "soft":
            if term_lower not in origin_soft_skills and not term_in_list(term, master_resume.get("skills", {}).get("soft_skills", [])):
                add_unconfirmed_skill(term)

    # Check keywords
    for keyword_req in extract.get("required_keywords", []):
        term = keyword_req["term"]
        term_lower = term.lower()
        if term_lower not in origin_keywords and not term_in_list(term, master_resume.get("keywords", [])):
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
    Filter experience bullets to keep only those which use skills or keywords present in extract,
    consider synonyms;
    Rank skills and keywords by priority from extract;
    Return filtered and re-ranked adapted resume.
    """
    # Build a set of all relevant terms from extract with synonyms
    relevant_terms = set()
    for group in ["required_skills", "required_keywords"]:
        for item in extract.get(group, []):
            # add main term and all synonyms lowercased
            relevant_terms.add(item["term"].lower())
            for syn in item.get("synonyms", []):
                relevant_terms.add(syn.lower())

    # Filter bullets with any skill or keyword in relevant_terms
    adapted_experience = []
    for exp in master_resume.get("experience", []):
        filtered_bullets = []
        for bullet in exp.get("bullets", []):
            bullet_skills = [s.lower() for s in bullet.get("skills_used", [])]
            bullet_keywords = [k.lower() for k in bullet.get("keyword_used", [])]
            if any(term in relevant_terms for term in bullet_skills + bullet_keywords):
                filtered_bullets.append(bullet)
        if filtered_bullets:
            exp_copy = exp.copy()
            exp_copy["bullets"] = filtered_bullets
            adapted_experience.append(exp_copy)

    # Sort skills and keywords by priority from extract; keep only relevant
    def sort_terms_by_priority(terms, extract_list):
        extract_priority_map = {item["term"].lower(): item.get("priority", 1000) for item in extract_list}
        # Keep only those in extract and sort by priority ascending
        filtered = [t for t in terms if t["term"].lower() in extract_priority_map]
        return sorted(filtered, key=lambda t: extract_priority_map[t["term"].lower()])

    adapted_hard_skills = sort_terms_by_priority(
        [s for s in master_resume.get("skills", {}).get("hard_skills", [])], extract.get("required_skills", [])
    )
    adapted_soft_skills = sort_terms_by_priority(
        [s for s in master_resume.get("skills", {}).get("soft_skills", [])], extract.get("required_skills", [])
    )
    adapted_keywords = sort_terms_by_priority(
        master_resume.get("keywords", []), extract.get("required_keywords", [])
    )

    # Build adapted master resume copy
    adapted_master = master_resume.copy()
    adapted_master["experience"] = adapted_experience
    adapted_master["skills"]["hard_skills"] = adapted_hard_skills
    adapted_master["skills"]["soft_skills"] = adapted_soft_skills
    adapted_master["keywords"] = adapted_keywords

    return adapted_master
