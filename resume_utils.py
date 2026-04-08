from fastapi import HTTPException
import json
from collections import defaultdict, Counter
import copy
from itertools import combinations
from math import floor, ceil
from datetime import datetime, date
from huggingface_hub import InferenceClient
import os
import requests
import re
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from typing import Dict, Any, List


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

        # Skip if any candidate is in explicitly_not_used
        if any(c in explicitly_not_used_keywords for c in candidates_lower):
            continue

        # Check confirmed sets + raw list in master
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
    """Рекурсивный перевод в JSON-сериализуемую структуру."""
    if isinstance(obj, dict):
        return {str(k): _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, set):
        # сортируем для детерминированности
        return [_make_serializable(v) for v in sorted(obj, key=lambda x: str(x))]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return repr(obj)

def debug_log(debug_info, name, obj, as_text=False, head=None, limit=None):
    """Безопасно кладёт снимок obj в debug_info[name]."""
    try:
        snapshot = copy.deepcopy(obj)
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
    Совместимая версия filter_and_rank_bullets с улучшенным алгоритмом выбора буллетов.
    """
    master_resume = copy.deepcopy(master_resume) #перед нормализацией терминов, чтоб потом не переписывать весь код использующий мастер

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

    # ---------- 1.1. Нормализация терминов в мастер-резюме ----------
    def normalize_term(term):
        """Возвращает корневой термин, если есть в term_to_root, иначе сам термин."""
        if not isinstance(term, str):
            return term
        t_lower = term.lower()
        return term_to_root.get(t_lower, term)


    for section in ["skills", "keywords"]:
        if section in master_resume:
            if section == "skills":
                for typ in ["hard_skills", "soft_skills"]:
                    if typ in master_resume["skills"]:
                        for skill in master_resume["skills"][typ]:
                            skill["term"] = normalize_term(skill.get("term"))
            elif section == "keywords":
                for kw in master_resume["keywords"]:
                    kw["term"] = normalize_term(kw.get("term"))

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
    bullet_index = {}  # id -> bullet object

    def unique_preserve_order(seq):
        """Удаляет дубликаты, сохраняя порядок появления."""
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    # сохраняем порядок компаний индексами
    for idx, exp in enumerate(master_resume.get("experience", [])):
        company_key = idx
        bullets_by_company.setdefault(company_key, [])

        for b in exp.get("bullets", []):
            bullet_copy = copy.deepcopy(b)

            # Сохраним оригинальные (до нормализации) множества для принятия решения о том, куда вернуть термин
            orig_skills = [s for s in b.get("skills_used", [])]
            orig_keywords = [k for k in b.get("keyword_used", [])]

            # Нормализуем термины (синонимы -> root)
            norm_skills = [term_to_root.get(t.lower(), t) for t in orig_skills]
            norm_keywords = [term_to_root.get(k.lower(), k) for k in orig_keywords]

            # Объединяем списки в порядке появления: сначала нормализованные skills, затем keywords
            # (сохраняем порядок появления внутри каждой секции)
            merged = norm_skills + norm_keywords

            # Дедупликация по порядку: если термин встречается и в skills, и в keywords,
            # он останется в той секции, где был изначально (мы помним orig_skills/orig_keywords)
            deduped = []
            seen = set()

            # Для быстрого определения: множества нормализованных исходных секций
            norm_skills_set = set(norm_skills)
            norm_keywords_set = set(norm_keywords)

            for term in merged:
                if term in seen:
                    continue
                seen.add(term)
                deduped.append(term)

            # Теперь распределяем обратно: предпочитаем skills, затем keywords
            new_skills = []
            new_keywords = []

            for term in deduped:
                # если исходно этот нормализованный термин был в skills (в orig_skills mapped),
                # то положим его в skills; иначе — в keywords
                if term in norm_skills_set:
                    new_skills.append(term)
                elif term in norm_keywords_set:
                    new_keywords.append(term)
                else:
                    # редкий случай: термин появился в merged, но не попал в ни одну из исходных нормализованных множеств
                    # (например, нормализация создала root, который не был в orig lists) — в таком случае
                    # логично положить его в skills по умолчанию (или в keywords, если хочешь)
                    new_skills.append(term)

            # Сохраняем в копии буллета
            bullet_copy["skills_used"] = unique_preserve_order(new_skills)
            bullet_copy["keyword_used"] = unique_preserve_order(new_keywords)

            bullets_by_company[company_key].append(bullet_copy)

            bid = bullet_copy.get("id")
            if bid is not None:
                bullet_to_company[bid] = company_key
                bullet_index[bid] = bullet_copy

    # ---------- 4. Определяем множества терминов ----------
    mandatory_terms = set(
        term_to_root.get(t.lower(), t)
        for t in (extract.get("mandatory", {}).get("skills", []) + extract.get("mandatory", {}).get("keywords", []))
    )
    nice_terms = set(
        term_to_root.get(t.lower(), t)
        for t in (extract.get("nice_to_have", {}).get("skills", []) + extract.get("nice_to_have", {}).get("keywords", []))
    )
    resume_terms = set(t.lower() for t in full_skill_pool.keys())
    mandatory_terms = set(t for t in mandatory_terms if t.lower() in resume_terms)
    nice_terms = set(t for t in nice_terms if t.lower() in resume_terms)

    # ---------- PREP: flatten bullets and build term->bullets map ----------
    all_bullets = []          # list of bullet dicts (objects)
    all_bullet_ids = []       # list of bullet ids in same order
    term_to_bullets = defaultdict(set)  # term(root) -> set(bullet_id)
    for company_key, bullets in bullets_by_company.items():
        for b in bullets:
            bid = b.get("id")
            if bid is None:
                continue
            all_bullets.append(b)
            all_bullet_ids.append(bid)
            terms = b.get("skills_used", []) + b.get("keyword_used", [])
            for t in terms:
                term_to_bullets[t].add(bid)

    # ---------- PHASE A: weight model and candidate collection ----------
    MAX_TERMS = 25
    MAX_TERMS_PER_BULLET = 3

    # compute term weights
    priority_weight = {}
    for t in set(list(term_to_bullets.keys())) | mandatory_terms | nice_terms:
        tl = t.lower()
        if t in mandatory_terms or tl in (x.lower() for x in mandatory_terms):
            pw = 3
        elif t in nice_terms or tl in (x.lower() for x in nice_terms):
            pw = 2
        else:
            pw = 1
        priority_weight[t] = pw

    term_count = {t: max(1, len(term_to_bullets.get(t, []))) for t in set(priority_weight.keys())}
    rarity_weight = {t: 1.0 / term_count[t] for t in term_count}

    alpha = 1.0
    beta = 0.5

    term_weight = {t: alpha * priority_weight.get(t, 1) + beta * rarity_weight.get(t, 0) for t in priority_weight}

    # compute bullet weights
    bullet_weight = {}
    for bid in all_bullet_ids:
        b = bullet_index[bid]
        terms = b.get("skills_used", []) + b.get("keyword_used", [])
        w = sum(term_weight.get(t, 0) for t in terms)
        # penalty for too many terms (soft)
        if len(terms) > MAX_TERMS_PER_BULLET:
            gamma = 0.06
            w *= (1 - gamma * (len(terms) - MAX_TERMS_PER_BULLET))
        bullet_weight[bid] = w

    # A3: collect candidate bullets for mandatory (not finalizing removal)
    candidate_bullets = set()
    for t in mandatory_terms:
        candidates = term_to_bullets.get(t, set())
        if not candidates:
            continue
        max_w = max(bullet_weight.get(bid, 0) for bid in candidates)
        threshold = 0.7 * max_w if max_w > 0 else 0
        # add bullets close to max weight to keep alternatives
        for bid in candidates:
            if bullet_weight.get(bid, 0) >= threshold:
                candidate_bullets.add(bid)

    # A4: try to add bullets to cover nice_terms (and extend candidate pool)
    # we prefer bullets that add most NEW term_weight
    selected_bullet_ids = set()
    selected_term_set = set()

    # --- 1. Add all mandatory bullets, но без расширения мусором ---
    for t in mandatory_terms:
        for bid in term_to_bullets.get(t, []):
            selected_bullet_ids.add(bid)
            b = bullet_index[bid]
            # добавляем только сами mandatory термины, без примесей
            selected_term_set.update([x for x in (b.get("skills_used", []) + b.get("keyword_used", [])) if x in mandatory_terms])

    # --- 2. Добавляем nice_terms, если есть место ---
    for t in nice_terms:
        if len(selected_term_set) >= MAX_TERMS:
            break
        for bid in term_to_bullets.get(t, []):
            b = bullet_index[bid]
            new_terms = [x for x in (b.get("skills_used", []) + b.get("keyword_used", [])) if x in nice_terms and x not in selected_term_set]
            if new_terms:
                selected_bullet_ids.add(bid)
                selected_term_set.update(new_terms)
                if len(selected_term_set) >= MAX_TERMS:
                    break
    # Add nice -> choose bullets that bring new coverage by weighted gain
    def weighted_gain_for_bullet(bid, current_terms):
        b = bullet_index[bid]
        terms = b.get("skills_used", []) + b.get("keyword_used", [])
        gain = sum(term_weight.get(t, 0) for t in terms if t not in current_terms)
        return gain

    # candidate pool initially contains mandatory candidates + all bullets that contain at least one mandatory or nice term
    for t in nice_terms:
        for bid in term_to_bullets.get(t, []):
            candidate_bullets.add(bid)

    # greedy add from candidate_bullets to improve coverage until reach MAX_TERMS or no gain
    all_candidate_list = set(candidate_bullets) | set(all_bullet_ids)
    # but limit to not explode: keep all bullets that contain any term in mandatory/nice/first-k optional
    # get optional candidates sample
    optional_pool = [t for t in resume_terms if t not in mandatory_terms and t not in nice_terms]
    optional_sample_terms = set(optional_pool[:200])  # limit
    for t in optional_sample_terms:
        for bid in term_to_bullets.get(t, []):
            all_candidate_list.add(bid)

    # Greedy: while we can add bullets that increase selected_terms by positive weighted gain
    improved = True
    while improved and len(selected_term_set) < MAX_TERMS:
        improved = False
        best_bid = None
        best_gain = 0.0
        for bid in all_candidate_list - selected_bullet_ids:
            gain = weighted_gain_for_bullet(bid, selected_term_set)
            if gain > best_gain:
                best_gain = gain
                best_bid = bid
        if best_bid and best_gain > 0:
            selected_bullet_ids.add(best_bid)
            selected_term_set.update(bullet_index[best_bid].get("skills_used", []) + bullet_index[best_bid].get("keyword_used", []))
            improved = True
        else:
            break

    # After greedy, ensure all mandatory covered (if still missing, add bullets that cover missing mandatory)
    missing_mandatory = [t for t in mandatory_terms if t not in selected_term_set]
    for t in missing_mandatory:
        # try to pick bullet with best bullet_weight among its bullets
        candidates = term_to_bullets.get(t, set())
        if candidates:
            best_bid = max(candidates, key=lambda b: bullet_weight.get(b, 0))
            if best_bid not in selected_bullet_ids:
                selected_bullet_ids.add(best_bid)
                selected_term_set.update(bullet_index[best_bid].get("skills_used", []) + bullet_index[best_bid].get("keyword_used", []))

    # A5: attempt to add optional terms up to MAX_TERMS by adding best-gain bullets
    # collect remaining optional terms
    optional_terms_all = [t for t in resume_terms if t not in selected_term_set]
    # greedy until reach MAX_TERMS
    while len(selected_term_set) < MAX_TERMS:
        best_bid = None
        best_gain = 0.0
        for bid in all_candidate_list - selected_bullet_ids:
            gain = weighted_gain_for_bullet(bid, selected_term_set)
            if gain > best_gain:
                best_gain = gain
                best_bid = bid
        if best_bid and best_gain > 0:
            selected_bullet_ids.add(best_bid)
            selected_term_set.update(bullet_index[best_bid].get("skills_used", []) + bullet_index[best_bid].get("keyword_used", []))
        else:
            break

    # ---------- PHASE B: trimming, restoration, apply company caps ----------
    # first, construct selected bullets list
    selected_bullets = [bullet_index[bid] for bid in sorted(selected_bullet_ids)]

    # B1: compute initial term coverage counts
    term_coverage_count = Counter()
    for b in selected_bullets:
        for t in b.get("skills_used", []) + b.get("keyword_used", []):
            term_coverage_count[t] += 1

    # B2: soft trim each bullet to MAX_TERMS_PER_BULLET by choosing top terms by term_weight (priority + rarity)
    for b in selected_bullets:
        terms = b.get("skills_used", []) + b.get("keyword_used", [])
        if len(terms) > MAX_TERMS_PER_BULLET:
            # sort by (term_weight, then priority) descending
            terms_sorted = sorted(terms, key=lambda t: (term_weight.get(t, 0), priority_weight.get(t, 1)), reverse=True)
            chosen = terms_sorted[:MAX_TERMS_PER_BULLET]
            # update coverage counters
            for t in set(terms) - set(chosen):
                term_coverage_count[t] -= 1
            # replace terms in bullet (keep type separation)
            b["skills_used"] = [t for t in chosen if skill_type_map.get(t.lower()) in ["hard", "soft"]]
            b["keyword_used"] = [t for t in chosen if skill_type_map.get(t.lower()) == "keyword"]
        else:
            # keep as is (but ensure they are in selected_term_set)
            b["skills_used"] = [t for t in b.get("skills_used", []) if t in selected_term_set]
            b["keyword_used"] = [t for t in b.get("keyword_used", []) if t in selected_term_set]

    # B3: find lost terms (coverage 0) and try to restore by adding bullets (prefer best bullet_weight)
    lost_terms = [t for t, cnt in term_coverage_count.items() if cnt <= 0 and t in selected_term_set]
    restored = []
    for t in lost_terms:
        candidates = term_to_bullets.get(t, set())
        # pick best candidate not already selected
        best_bid = None
        best_w = -1
        for bid in candidates:
            if bid in selected_bullet_ids:
                continue
            w = bullet_weight.get(bid, 0)
            if w > best_w:
                best_w = w
                best_bid = bid
        if best_bid:
            # add bullet back (even if this may later be pruned by company caps)
            selected_bullet_ids.add(best_bid)
            selected_bullets.append(bullet_index[best_bid])
            for tt in bullet_index[best_bid].get("skills_used", []) + bullet_index[best_bid].get("keyword_used", []):
                term_coverage_count[tt] += 1
            restored.append((t, best_bid))

    # B4: apply per-company caps NOW (final pruning)
    # first compute company_caps if not already (we computed earlier in older code; recompute reliably)
    company_caps = {}
    for idx, exp in enumerate(master_resume.get("experience", [])):
        duration_years = exp.get("duration_years", 0)
        cap = ceil(duration_years) + 1 if duration_years - floor(duration_years) >= 0.5 else floor(duration_years) + 1
        company_caps[idx] = cap

    # group selected bullets by company_key
    sel_by_company = defaultdict(list)
    for bid in list(selected_bullet_ids):
        b = bullet_index.get(bid)
        if not b:
            continue
        ck = bullet_to_company.get(bid)
        sel_by_company[ck].append(b)

    # For each company, if too many bullets -> iteratively remove bullets with minimal penalty
    for ck, blist in sel_by_company.items():
        cap = company_caps.get(ck, 1)
        while len(blist) > cap:
            loss_scores = {}
            for b in blist:
                bid = b.get("id")
                terms = b.get("skills_used", []) + b.get("keyword_used", [])

                # --- 1. Считаем приоритет экстракта ---
                extract_term_priorities = [priority_map.get(t.lower(), None) for t in terms if t in mandatory_terms or t in nice_terms]
                if extract_term_priorities:
                    # защищаем максимальный приоритет
                    max_priority = min(extract_term_priorities)  # 1 = highest priority
                    # считаем количество уникальных вымирающих терминов (coverage 1)
                    dying_terms_count = sum(1 for t in terms if term_coverage_count.get(t, 0) == 1 and t in mandatory_terms | nice_terms)
                    # комбинированный loss: чем выше приоритет и больше вымирающих терминов, тем выше защита
                    loss = max_priority * 10000 + dying_terms_count * 1000
                else:
                    # --- 2. Если терминов экстракта нет — используем старый весовой метод ---
                    loss = 0.0
                    for t in terms:
                        w = term_weight.get(t, 0)
                        if term_coverage_count.get(t, 0) == 1:
                            loss += w * 1000.0
                        else:
                            loss += w
                    # optional: корректируем за длину буллета
                    if len(terms) > 1:
                        loss = loss / (len(terms) ** 0.3)

                loss_scores[bid] = loss

            # pick bullet with minimal loss (tie-breaker: lower bullet_weight)
            bid_to_remove = min(loss_scores.keys(), key=lambda bid: (loss_scores[bid], bullet_weight.get(bid, 0)))

            # remove it from blist and update structures
            brem = next((b for b in blist if b.get("id") == bid_to_remove), None)
            if not brem:
                break
            blist.remove(brem)
            selected_bullet_ids.discard(bid_to_remove)
            for t in brem.get("skills_used", []) + brem.get("keyword_used", []):
                term_coverage_count[t] -= 1
        sel_by_company[ck] = blist

    # Rebuild final_bullets list from selected_bullet_ids, preserving original company order and per-company sorting
    final_bullets = [bullet_index[bid] for bid in sorted(selected_bullet_ids) if bid in bullet_index]
    # ensure each bullet only contains terms that remain covered and in selected_term_set
    for b in final_bullets:
        terms = [t for t in (b.get("skills_used", []) + b.get("keyword_used", [])) if term_coverage_count.get(t, 0) > 0 and t in selected_term_set]
        # again trim to MAX_TERMS_PER_BULLET by weight (safety)
        terms_sorted = sorted(terms, key=lambda t: (term_weight.get(t, 0), priority_weight.get(t,1)), reverse=True)
        chosen = terms_sorted[:MAX_TERMS_PER_BULLET]
        b["skills_used"] = [t for t in chosen if skill_type_map.get(t.lower()) in ["hard", "soft"]]
        b["keyword_used"] = [t for t in chosen if skill_type_map.get(t.lower()) == "keyword"]

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

    def bullet_sort_key(b, priority_map, mandatory_terms, nice_terms, bullet_weight):
        """
        Ключ для сортировки буллетов внутри компании.
        1) Сначала буллеты, которые содержат термины экстракта (mandatory/nice_to_have),
        сортируются по минимальному приоритету среди этих терминов.
        2) Если нет терминов экстракта, сортируются по весу bullet_weight.
        """
        terms = b.get("skills_used", []) + b.get("keyword_used", [])
        extract_terms = [t for t in terms if t in mandatory_terms or t in nice_terms]
        extract_priorities = [priority_map.get(t.lower(), 1000) for t in extract_terms]
        
        if extract_priorities:
            return (0, min(extract_priorities))  # приоритет по экстракту
        else:
            return (1, bullet_weight.get(b.get("id"), 0))  # сортировка по весу

    for company in copy.deepcopy(master_resume.get("experience", [])):
        original_ids = [b.get("id") for b in company.get("bullets", []) if "id" in b]
        filtered_bullets = [bullet_map[bid] for bid in original_ids if bid in bullet_map]
        
        if filtered_bullets:
            filtered_bullets.sort(
                key=lambda b: bullet_sort_key(b, priority_map, mandatory_terms, nice_terms, bullet_weight)
            )
            company["bullets"] = filtered_bullets
            restored_experience.append(company)

    # ---------- 12. Формируем итог с сортировкой по приоритету ----------
    adapted_resume = copy.deepcopy(master_resume)

    def sort_adapted_terms(adapted_dict):
        """
        Сортирует термины по приоритету: сначала термины из экстракта по убыванию важности,
        затем опциональные термины, которых нет в экстракте.
        """
        def term_priority(item):
            term_l = item["term"].lower()
            return priority_map.get(term_l, 1000)  # 1000+ для опциональных
        return sorted(adapted_dict.values(), key=term_priority)

    adapted_resume["skills"] = {
        "hard_skills": sort_adapted_terms(adapted_hard),
        "soft_skills": sort_adapted_terms(adapted_soft)
    }
    adapted_resume["keywords"] = sort_adapted_terms(adapted_keywords)
    adapted_resume["experience"] = restored_experience

    if "job_title" in extract:
        adapted_resume["desired_positions"] = [extract["job_title"]]

    return adapted_resume


def unconfirmed2terms(input_data):
    terms = []

    skills = input_data.get("skills", {})

    for skill in skills.get("hard_skills", []):
        if skill.get("confirmed_by") == []:
            terms.append({
                "term": skill.get("term", ""),
                "type": "hard",
                "generated_bullet": "",
            })

    for skill in skills.get("soft_skills", []):
        if skill.get("confirmed_by") == []:
            terms.append({
                "term": skill.get("term", ""),
                "type": "soft",
                "generated_bullet": "",
            })

    for keyword in input_data.get("keywords", []):
        if keyword.get("confirmed_by") == []:
            terms.append({
                "term": keyword.get("term", ""),
                "type": "keyword",
                "generated_bullet": "",
            })

    return {"terms": terms}

def btnsCompany(data: dict) -> dict:
    companies = data.get("companies", [])
    
    inline_keyboard = [[{"text": c, "callback_data": c}] for c in companies]

    # добавляем кнопку NO последней
    inline_keyboard.append([{"text": "No experience", "callback_data": "no_experience"}])
    
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
       (замечание: в новой версии мы очищаем confirmed_by и не синхронизируем обратно,
       потому что в требованиях сказано гарантировать пустые confirmed_by).
    3. Проверяет обратную связь — что все термины, перечисленные в буллетах,
       упомянуты в confirmed_by в секции skills/keywords. (в рамках требований
       термы из буллетов, не найдённые в секциях — попадают в unknown.*)
    Дополнительно: удаление дубликатов буллетов, перенумерация id, duration_years и т.д.
    """
    # --- Утилиты ---
    def add_https_if_missing(url: str) -> str:
        if not isinstance(url, str):
            return ""
        url = url.strip()
        if url and not (url.startswith("http://") or url.startswith("https://")):
            return "https://" + url
        return url

    def try_parse_date(s: str):
        """
        Попытка распарсить дату в нескольких распространённых форматах.
        Возвращает date или None. Поддерживает "Present"/"now" -> handled отдельно.
        """
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        if not s:
            return None
        # common representations
        patterns = [
            "%b %Y",   # Nov 2020
            "%B %Y",   # November 2020
            "%Y-%m-%d",
            "%Y-%m",
            "%Y",
            "%m/%Y",
            "%m/%d/%Y",
            "%d %b %Y",
            "%d %B %Y",
        ]
        for p in patterns:
            try:
                dt = datetime.strptime(s, p)
                return dt.date()
            except Exception:
                continue
        # try parsing "Month YYYY" with possible trailing commas or words
        try:
            # handle formats like "Nov. 2020" or "Nov, 2020"
            cleaned = s.replace(".", "").replace(",", "")
            for p in ("%b %Y", "%B %Y"):
                try:
                    dt = datetime.strptime(cleaned, p)
                    return dt.date()
                except Exception:
                    pass
        except Exception:
            pass
        return None

    today = date.today()

    # --- Шаг 0: подготовка удобных ссылок (и гарантия поля personal_info) ---
    if "personal_info" not in master_resume or not isinstance(master_resume.get("personal_info"), dict):
        master_resume["personal_info"] = {}

    pi = master_resume["personal_info"]
    pi["linkedin"] = add_https_if_missing(pi.get("linkedin", "") or "")
    pi["portfolio"] = add_https_if_missing(pi.get("portfolio", "") or "")

    # --- Шаг 1: гарантия наличия секций и удобных ссылок на списки ---
    if "skills" not in master_resume or not isinstance(master_resume.get("skills"), dict):
        master_resume["skills"] = {"hard_skills": [], "soft_skills": []}
    skills = master_resume["skills"]
    hard_skills: List[Dict[str, Any]] = skills.get("hard_skills") or []
    soft_skills: List[Dict[str, Any]] = skills.get("soft_skills") or []

    keywords: List[Dict[str, Any]] = master_resume.get("keywords") or []
    experience: List[Dict[str, Any]] = master_resume.get("experience") or []

    # ensure unconfirmed and explicitly_not_used exist (пустые списки при отсутствии)
    if "unconfirmed" not in master_resume or not isinstance(master_resume.get("unconfirmed"), dict):
        master_resume["unconfirmed"] = {"skills": [], "keywords": []}
    else:
        master_resume["unconfirmed"].setdefault("skills", [])
        master_resume["unconfirmed"].setdefault("keywords", [])

    if "explicitly_not_used" not in master_resume or not isinstance(master_resume.get("explicitly_not_used"), dict):
        master_resume["explicitly_not_used"] = {"skills": [], "keywords": []}
    else:
        master_resume["explicitly_not_used"].setdefault("skills", [])
        master_resume["explicitly_not_used"].setdefault("keywords", [])

    # --- Шаг 2: удаление дубликатов буллетов и сквозная нумерация ID ---
    # Удаляем полностью повторяющиеся буллеты по полю text (сохраняем первую встречу).
    seen_texts = set()
    next_bullet_id = 1
    for exp in experience:
        new_bullets = []
        for bullet in exp.get("bullets", []) or []:
            text = (bullet.get("text") or "").strip()
            if text == "":
                # если текст пустой — считаем его допустимым, но всё ещё фильтруем по точному совпадению
                pass
            if text in seen_texts:
                # дубликат — пропускаем
                continue
            seen_texts.add(text)
            # make a shallow copy to avoid mutating original references unexpectedly
            b = dict(bullet)
            # перезаписываем id сквозной нумерацией
            b["id"] = next_bullet_id
            next_bullet_id += 1
            # гарантируем поля lists
            b.setdefault("skills_used", [])
            b.setdefault("keyword_used", [])
            new_bullets.append(b)
        exp["bullets"] = new_bullets

    # --- Шаг 3: гарантируем, что confirmed_by у всех скиллов/кейвордс пустые списки ---
    def ensure_confirmed_by_empty(lst: List[Dict[str, Any]]):
        for item in lst:
            item["confirmed_by"] = []

    ensure_confirmed_by_empty(hard_skills)
    ensure_confirmed_by_empty(soft_skills)
    for kw in keywords:
        kw["confirmed_by"] = []

    # --- Шаг 4: удалить дубликаты терминов между hard / soft / keywords ---
    # Правило: если термин в hard — удаляем все вхождения в soft; если термин в (hard или soft) — удаляем из keywords.
    # Сравнение терминов делаем по точной строке (case sensitive?). Сделаем нормализацию: strip().
    def normalize_term(t):
        return t.strip() if isinstance(t, str) else t

    hard_terms = []
    for s in hard_skills:
        term = normalize_term(s.get("term", ""))
        s["term"] = term
        hard_terms.append(term)
    hard_set = set(filter(None, hard_terms))

    # Filter soft: remove any whose term is in hard_set
    new_soft = []
    for s in soft_skills:
        term = normalize_term(s.get("term", ""))
        s["term"] = term
        if term and term in hard_set:
            # удаляем дубликат из soft
            continue
        new_soft.append(s)
    soft_skills[:] = new_soft  # in-place replace

    soft_terms = [s.get("term", "") for s in soft_skills]
    soft_set = set(filter(None, [t.strip() for t in soft_terms]))

    # Filter keywords: remove any that appear in hard_set or soft_set
    new_keywords = []
    for k in keywords:
        term = normalize_term(k.get("term", ""))
        k["term"] = term
        if term and (term in hard_set or term in soft_set):
            continue
        new_keywords.append(k)
    keywords[:] = new_keywords  # in-place replace

    # --- Шаг 5: обработка терминов из буллетов ---
    # - неизвестные skills_used → в unknown.skills
    # - неизвестные keyword_used → добавлять в keywords как полноценные записи
    # - unknown.keywords больше НЕ используется

    # Наборы известных терминов
    known_skill_terms = set(
        normalize_term(s.get("term", ""))
        for s in (hard_skills + soft_skills)
        if normalize_term(s.get("term", ""))
    )
    known_keyword_terms = set(
        normalize_term(k.get("term", ""))
        for k in keywords
        if normalize_term(k.get("term", ""))
    )

    unknown_skills = set()

    for exp in experience:
        for bullet in exp.get("bullets", []) or []:
            
            # --- skills_used ---
            for term in bullet.get("skills_used", []) or []:
                t = normalize_term(term)
                if not t:
                    continue
                # если термин не найден ни в skill-секциях, ни в keywords → unknown.skills
                if t not in known_skill_terms and t not in known_keyword_terms:
                    unknown_skills.add(t)

            # --- keyword_used ---
            for term in bullet.get("keyword_used", []) or []:
                t = normalize_term(term)
                if not t:
                    continue
                # если термина нет в keywords → добавляем заготовку
                if t not in known_keyword_terms:
                    keywords.append({
                        "term": t,
                        "confirmed_by": [],
                        "origin": True
                    })
                    known_keyword_terms.add(t)  # чтобы не добавлять повторно

    # Создание секции unknown только для skills
    if unknown_skills:
        master_resume.setdefault("unknown", {})
        master_resume["unknown"]["skills"] = sorted(unknown_skills)
        # unknown[keywords] больше не используется
    else:
        # гарантируем корректную структуру, если она вдруг уже существовала
        if "unknown" in master_resume:
            master_resume["unknown"].setdefault("skills", [])

    # --- Шаг 6: вычисление duration_years для каждой компании на основании start_date/end_date ---
    for exp in experience:
        start_raw = exp.get("start_date", "") or ""
        end_raw = exp.get("end_date", "") or ""

        start_parsed = None
        end_parsed = None

        # handle present/now
        if isinstance(end_raw, str) and end_raw.strip().lower() in ("present", "now"):
            end_parsed = today
        else:
            end_parsed = try_parse_date(end_raw)

        if isinstance(start_raw, str) and start_raw.strip().lower() in ("present", "now"):
            # start == present — treat as today
            start_parsed = today
        else:
            start_parsed = try_parse_date(start_raw)

        duration_years = None
        if (start_parsed is None or start_parsed == "") and (end_parsed is None or end_parsed == ""):
            # если обе даты пустые или нераспарсились — ставим 1
            duration_years = 1.0
        else:
            # если одна из дат отсутствует — подставляем today для вычисления (если end отсутствует) или
            # используем start_parsed и end_parsed если есть хотя бы одна
            if start_parsed is None and end_parsed is not None:
                # не знаем начало — предположим год до end (1 год)
                # альтернативно можно взять 1.0 как минимальный
                duration_years = 1.0
            elif start_parsed is not None and end_parsed is None:
                # нет конца — считаем до сегодня
                delta_days = (today - start_parsed).days
                if delta_days < 0:
                    # на случай ошибок в датах
                    duration_years = 1.0
                else:
                    duration_years = round(delta_days / 365.0, 1)
                    if duration_years < 0.1:
                        duration_years = 0.1
            else:
                # оба присутствуют
                try:
                    delta_days = (end_parsed - start_parsed).days
                    if delta_days < 0:
                        # некорректный период — минимизируем до 0.1 или 1
                        duration_years = 1.0
                    else:
                        duration_years = round(delta_days / 365.0, 1)
                        if duration_years < 0.1:
                            duration_years = 0.1
                except Exception:
                    duration_years = 1.0

        exp["duration_years"] = duration_years

    # --- Шаг 7: восстановление секции unconfirmed на основе пустого confirmed_by ---
    unconfirmed_skills = set(master_resume.get("unconfirmed", {}).get("skills", []))
    unconfirmed_keywords = set(master_resume.get("unconfirmed", {}).get("keywords", []))

    for s in hard_skills:
        if not s.get("confirmed_by"):
            term = normalize_term(s.get("term", ""))
            if term:
                unconfirmed_skills.add(term)

    for s in soft_skills:
        if not s.get("confirmed_by"):
            term = normalize_term(s.get("term", ""))
            if term:
                unconfirmed_skills.add(term)

    for k in keywords:
        if not k.get("confirmed_by"):
            term = normalize_term(k.get("term", ""))
            if term:
                unconfirmed_keywords.add(term)

    master_resume["unconfirmed"] = {
        "skills": sorted(unconfirmed_skills),
        "keywords": sorted(unconfirmed_keywords)
    }

    # --- Финальные гарантии: убедимся, что все перечисляемые секции присутствуют в нужном виде ---
    master_resume.setdefault("desired_positions", master_resume.get("desired_positions", []))
    master_resume.setdefault("education", master_resume.get("education", []))
    master_resume.setdefault("certifications", master_resume.get("certifications", []))
    master_resume.setdefault("languages", master_resume.get("languages", []))

    # Обновим ссылки на списки в документе (на случай, если мы меняли сами списки)
    master_resume["skills"]["hard_skills"] = hard_skills
    master_resume["skills"]["soft_skills"] = soft_skills
    master_resume["keywords"] = keywords
    master_resume["experience"] = experience

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
        lines.append(f"\n[[h2]]Work experience")

    for exp in experience:
        company = exp.get("company", "").strip()
        job_title = exp.get("job_title", "").strip()
        location = exp.get("location", "").strip()
        start_date = exp.get("start_date", "").strip()
        end_date = exp.get("end_date", "").strip() or "Present"
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


ACTION_WORDS = [
    "developed", "implemented", "built", "designed", "optimized", "automated",
    "created", "integrated", "maintained", "improved", "deployed", "enhanced"
]

# токен храним в переменной окружения на Render
HF_TOKEN = os.getenv("HF_TOKEN")

# Клиент Hugging Face
client = InferenceClient(
    provider="hf-inference",
    api_key=os.getenv("HF_TOKEN"),
)

def get_semantic_similarity(job_text: str, resume_text: str) -> float:
    """Запрашивает у Hugging Face API семантическую схожесть между вакансия и резюме"""
    try:
        result = client.sentence_similarity(
            source_sentence=job_text,
            other_sentences=[resume_text],
            model="sentence-transformers/all-MiniLM-L6-v2",
        )
        # Возвращает число от 0 до 1
        return float(result[0])
    except Exception as e:
        print("HF API Error:", e)
        return 0.0
    

def context_weighting(text: str, keywords: list[str]) -> float:
    """Добавляет вес, если ключевые слова встречаются в активном контексте"""
    text = text.lower()
    score = 0
    total = 0
    for kw in keywords:
        kw = kw.lower()
        # Ищем фразы типа "developed ... python" или "python ... developed"
        for aw in ACTION_WORDS:
            if re.search(rf"{aw}\W+(?:\w+\W+){{0,5}}{kw}", text):
                score += 1.5  # бонус за применение
            elif re.search(rf"{kw}\W+(?:\w+\W+){{0,5}}{aw}", text):
                score += 1.5
        # если просто встречается
        if kw in text:
            score += 1
        total += 1
    return min(score / total, 1.0) if total > 0 else 0.0


def extract_keywords(text, top_n=30):
    """Простая эвристика для вытаскивания ключевых терминов."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s\-\+]', ' ', text)
    words = text.split()
    stopwords = set([
        'and','or','the','to','for','with','of','in','on','at','as','a','an',
        'by','from','this','that','is','are','be','have','has','was','will','we',
        'it','our','your','you','their','they','them','about','more','than'
    ])
    keywords = [w for w in words if len(w) > 2 and w not in stopwords]
    freq = {}
    for w in keywords:
        freq[w] = freq.get(w, 0) + 1
    sorted_kw = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [k for k, _ in sorted_kw[:top_n]]


def compute_ats_metrics(job_text, resume_text):
    """
    Оценивает схожесть резюме с вакансией:
    - ключевые слова (recall, precision)
    - семантическое сходство через HuggingFace модель или TF-IDF fallback
    """
    job_kw = extract_keywords(job_text)
    resume_kw = extract_keywords(resume_text)

    if not job_kw or not resume_kw:
        return {"ats_score": 0, "semantic": 0, "recall": 0, "precision": 0}

    # --- Ключевые метрики
    job_set, resume_set = set(job_kw), set(resume_kw)
    intersection = job_set & resume_set
    recall = len(intersection) / len(job_set)
    precision = len(intersection) / len(resume_set)

    # --- Семантическая близость (через HF API)
    semantic = get_semantic_similarity(job_text, resume_text)

    # fallback на TF-IDF, если HF не ответил
    if semantic == 0.0:
        vectorizer = TfidfVectorizer()
        tfidf = vectorizer.fit_transform([job_text, resume_text])
        semantic = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]

    # --- Итоговый ATS-скор
    ats_score = 100 * (0.55 * semantic + 0.25 * recall + 0.2 * precision)

    return {
        "ats_score(70-90)": round(float(ats_score), 2),
        "semantic(coverage_incl_synonyms_0.6-0.85)": round(float(semantic), 4),
        "recall(JD->CV_0.6-0.85)": round(float(recall), 4),
        "precision(density_of_terms_0.4-0.7)": round(float(precision), 4),
        "overlap_keywords": list(intersection),
        "job_keywords": job_kw,
        "resume_keywords": resume_kw,
    }


def analyze_job_description(job_description: str, extract: Dict[str, Any]) -> Dict[str, Any]:
    """
    Анализирует текст вакансии на наличие терминов из JSON-экстракта.
    Возвращает статистику совпадений и список недостающих терминов.
    """

    # Приводим текст вакансии к нижнему регистру для нечувствительности к регистру
    text = job_description.lower()

    def count_found(terms: List[str]) -> (int, List[str]):
        found = 0
        missing = []
        for term in terms:
            pattern = r"\b" + re.escape(term.lower()) + r"\b"
            if re.search(pattern, text):
                found += 1
            else:
                missing.append(term)
        return found, missing

    # Собираем списки из JSON
    mandatory_terms = extract.get("mandatory", {}).get("skills", []) + extract.get("mandatory", {}).get("keywords", [])
    nice_to_have_terms = extract.get("nice_to_have", {}).get("skills", []) + extract.get("nice_to_have", {}).get("keywords", [])

    total_terms = mandatory_terms + nice_to_have_terms

    # Считаем найденные термины
    mandatory_found, mandatory_missing = count_found(mandatory_terms)
    nice_found, nice_missing = count_found(nice_to_have_terms)

    total_found = mandatory_found + nice_found
    total_expected = len(total_terms)

    percent_found = round((total_found / total_expected) * 100, 2) if total_expected > 0 else 0.0

    result = {
        "match_percent": percent_found,  # общий процент
        "mandatory": f"{mandatory_found}/{len(mandatory_terms)}",
        "nice_to_have": f"{nice_found}/{len(nice_to_have_terms)}",
        "lost": mandatory_missing + nice_missing
    }

    return result

def skills2master(skills: dict, master_resume: dict) -> dict:
    """
    Merge classified skills (hard/soft) into master_resume.skills,
    following the master skill structure:
    [{"term": "", "confirmed_by": [], "origin": true}]
    Also removes added skills from master_resume.unknown.skills.
    """

    # Ensure required structures exist
    master_resume.setdefault("skills", {"hard_skills": [], "soft_skills": []})
    master_resume["skills"].setdefault("hard_skills", [])
    master_resume["skills"].setdefault("soft_skills", [])

    master_resume.setdefault("unknown", {})
    master_resume["unknown"].setdefault("skills", [])

    # Helper: create master skill object
    def make_skill_obj(term: str) -> dict:
        return {
            "term": term,
            "confirmed_by": [],
            "origin": True
        }

    # Add hard skills
    for term in skills.get("hard_skills", []):
        # avoid duplicates
        if not any(s["term"].lower() == term.lower() for s in master_resume["skills"]["hard_skills"]):
            master_resume["skills"]["hard_skills"].append(make_skill_obj(term))

        # remove from unknown.skills
        master_resume["unknown"]["skills"] = [
            s for s in master_resume["unknown"]["skills"]
            if s.lower() != term.lower()
        ]

    # Add soft skills
    for term in skills.get("soft_skills", []):
        if not any(s["term"].lower() == term.lower() for s in master_resume["skills"]["soft_skills"]):
            master_resume["skills"]["soft_skills"].append(make_skill_obj(term))

        master_resume["unknown"]["skills"] = [
            s for s in master_resume["unknown"]["skills"]
            if s.lower() != term.lower()
        ]

    return master_resume


def BulletsToButtons(data: dict) -> dict:
    bullets = data.get("bullets", [])
    inline_keyboard = []

    for item in bullets:
        text = item.get("text", "")
        callback = str(item.get("id", ""))

        inline_keyboard.append([
            {
                "text": text,
                "callback_data": callback
            }
        ])

    return {"inline_keyboard": inline_keyboard}


def Term_not_used(term_name: str, term_type: str, master_json: dict) -> dict:
    term_lower = term_name.lower()

    def remove_term(items):
        return [i for i in items if i.get("term", "").lower() != term_lower]

    # гарантируем наличие нужных секций
    master_json.setdefault("unconfirmed", {"skills": [], "keywords": []})
    master_json.setdefault("explicitly_not_used", {"skills": [], "keywords": []})

    if term_type in ("hard", "soft"):
        skill_section = "hard_skills" if term_type == "hard" else "soft_skills"

        # удалить из skills
        if "skills" in master_json and skill_section in master_json["skills"]:
            master_json["skills"][skill_section] = remove_term(
                master_json["skills"][skill_section]
            )

        # удалить из unconfirmed.skills
        master_json["unconfirmed"]["skills"] = remove_term(
            master_json["unconfirmed"]["skills"]
        )

        # добавить в explicitly_not_used.skills если нет
        if not any(
            x.get("term", "").lower() == term_lower
            for x in master_json["explicitly_not_used"]["skills"]
        ):
            master_json["explicitly_not_used"]["skills"].append({"term": term_name})

    elif term_type == "keyword":

        # удалить из keywords
        master_json["keywords"] = remove_term(master_json.get("keywords", []))

        # удалить из unconfirmed.keywords
        master_json["unconfirmed"]["keywords"] = remove_term(
            master_json["unconfirmed"]["keywords"]
        )

        # добавить в explicitly_not_used.keywords если нет
        if not any(
            x.get("term", "").lower() == term_lower
            for x in master_json["explicitly_not_used"]["keywords"]
        ):
            master_json["explicitly_not_used"]["keywords"].append({"term": term_name})

    else:
        raise ValueError("term_type must be 'hard', 'soft', or 'keyword'")

    return master_json

def GetCompanyBullets(master_json: dict, company_name: str):
    """
    Возвращает:
    - bullets_text (str)
    - bullets_menu (dict)
    """

    experience = master_json.get("experience", [])

    # нормализация входного названия
    target_company = (company_name or "").strip().lower()

    # Найти нужную компанию (регистронезависимо)
    company_data = None
    for job in experience:
        job_company = (job.get("company") or "").strip().lower()
        if job_company == target_company:
            company_data = job
            break

    if not company_data:
        return {
            "bullets_text": "",
            "bullets_menu": {"inline_keyboard": [[{"text": "Back", "callback_data": "Back"}]]}
        }

    bullets = company_data.get("bullets", [])

    # --- bullets_text ---
    lines = []
    l_ids = []

    for bullet in bullets:
        b_id = bullet.get("id")
        text = bullet.get("text", "")

        if b_id is None:
            continue

        lines.append(f"{b_id}. {text}")
        l_ids.append(b_id)

    bullets_text = "\n".join(lines)

    # --- bullets_menu ---
    inline_keyboard = []
    row = []

    for idx, b_id in enumerate(l_ids):
        row.append({
            "text": str(b_id),
            "callback_data": str(b_id)
        })

        if (idx + 1) % 3 == 0:
            inline_keyboard.append(row)
            row = []

    if row:
        inline_keyboard.append(row)

    inline_keyboard.append([
        {
            "text": "Back",
            "callback_data": "Back"
        }
    ])

    bullets_menu = {
        "inline_keyboard": inline_keyboard
    }

    return {
        "bullets_text": bullets_text,
        "bullets_menu": bullets_menu
    }


def confirm_term(master_json: dict, bullet_id: int, term_name: str, term_type: str) -> dict:
    """
    Confirms a skill or keyword with a specific bullet, establishing a two-way link.
    term_type: "hard" | "soft" | "keyword"
    """
    import copy
    master = copy.deepcopy(master_json)

    # --- 1. Find the bullet in experience ---
    target_bullet = None
    for job in master.get("experience", []):
        for bullet in job.get("bullets", []):
            if bullet.get("id") == bullet_id:
                target_bullet = bullet
                break
        if target_bullet:
            break

    if target_bullet is None:
        raise ValueError(f"Bullet with id={bullet_id} not found in experience.")

    # --- 2. Add term to bullet's skills_used or keyword_used ---
    if term_type in ("hard", "soft"):
        if term_name not in target_bullet.setdefault("skills_used", []):
            target_bullet["skills_used"].append(term_name)
    elif term_type == "keyword":
        if term_name not in target_bullet.setdefault("keyword_used", []):
            target_bullet["keyword_used"].append(term_name)
    else:
        raise ValueError(f"Invalid term_type='{term_type}'. Must be 'hard', 'soft', or 'keyword'.")

    # --- 3. Find or create the term entry; add bullet_id to confirmed_by ---
    if term_type == "hard":
        term_list = master["skills"]["hard_skills"]
    elif term_type == "soft":
        term_list = master["skills"]["soft_skills"]
    else:
        term_list = master["keywords"]

    term_entry = next((t for t in term_list if t.get("term") == term_name), None)

    if term_entry is None:
        # Create new entry without "origin": true
        term_entry = {"term": term_name, "confirmed_by": []}
        term_list.append(term_entry)

    if bullet_id not in term_entry.setdefault("confirmed_by", []):
        term_entry["confirmed_by"].append(bullet_id)

    # --- 4. Remove from unconfirmed if present ---
    unconfirmed = master.setdefault("unconfirmed", {"skills": [], "keywords": []})

    if term_type in ("hard", "soft"):
        unconfirmed["skills"] = [
            t for t in unconfirmed.get("skills", [])
            if t.get("term") != term_name
        ]
    else:
        unconfirmed["keywords"] = [
            t for t in unconfirmed.get("keywords", [])
            if t.get("term") != term_name
        ]

    # --- 5. Remove from explicitly_not_used if present ---
    not_used = master.setdefault("explicitly_not_used", {"skills": [], "keywords": []})

    if term_type in ("hard", "soft"):
        not_used["skills"] = [
            t for t in not_used.get("skills", [])
            if t.get("term") != term_name
        ]
    else:
        not_used["keywords"] = [
            t for t in not_used.get("keywords", [])
            if t.get("term") != term_name
        ]

    return master


def add_new_bullet(master_json: dict, company: str, bullet: str, term_name: str, term_type: str) -> dict:
    """
    Adds a new bullet to a company's experience entry and confirms the linked term.
    term_type: "hard" | "soft" | "keyword"
    """
    import copy
    master = copy.deepcopy(master_json)

    # --- 1. Find the company in experience ---
    target_job = next(
        (job for job in master.get("experience", []) if job.get("company") == company),
        None
    )

    if target_job is None:
        raise ValueError(f"Company '{company}' not found in experience.")

    if term_type not in ("hard", "soft", "keyword"):
        raise ValueError(f"Invalid term_type='{term_type}'. Must be 'hard', 'soft', or 'keyword'.")

    # --- 2. Calculate next bullet ID (global max across all jobs) ---
    all_ids = [
        b["id"]
        for job in master.get("experience", [])
        for b in job.get("bullets", [])
        if isinstance(b.get("id"), int)
    ]
    new_id = max(all_ids) + 1 if all_ids else 1

    # --- 3. Build and append the new bullet ---
    if term_type in ("hard", "soft"):
        new_bullet = {
            "id": new_id,
            "text": bullet,
            "skills_used": [term_name],
            "keyword_used": []
        }
    else:
        new_bullet = {
            "id": new_id,
            "text": bullet,
            "skills_used": [],
            "keyword_used": [term_name]
        }

    target_job.setdefault("bullets", []).append(new_bullet)

    # --- 4. Find or create term entry; add new_id to confirmed_by ---
    if term_type == "hard":
        term_list = master["skills"]["hard_skills"]
    elif term_type == "soft":
        term_list = master["skills"]["soft_skills"]
    else:
        term_list = master["keywords"]

    term_entry = next((t for t in term_list if t.get("term") == term_name), None)

    if term_entry is None:
        term_entry = {"term": term_name, "confirmed_by": [new_id]}
        term_list.append(term_entry)
    else:
        if new_id not in term_entry.setdefault("confirmed_by", []):
            term_entry["confirmed_by"].append(new_id)

    # --- 5. Remove from unconfirmed if present ---
    unconfirmed = master.setdefault("unconfirmed", {"skills": [], "keywords": []})

    if term_type in ("hard", "soft"):
        unconfirmed["skills"] = [
            t for t in unconfirmed.get("skills", [])
            if t.get("term") != term_name
        ]
    else:
        unconfirmed["keywords"] = [
            t for t in unconfirmed.get("keywords", [])
            if t.get("term") != term_name
        ]

    # --- 6. Remove from explicitly_not_used if present ---
    not_used = master.setdefault("explicitly_not_used", {"skills": [], "keywords": []})

    if term_type in ("hard", "soft"):
        not_used["skills"] = [
            t for t in not_used.get("skills", [])
            if t.get("term") != term_name
        ]
    else:
        not_used["keywords"] = [
            t for t in not_used.get("keywords", [])
            if t.get("term") != term_name
        ]

    return master