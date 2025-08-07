from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import json

app = FastAPI()

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