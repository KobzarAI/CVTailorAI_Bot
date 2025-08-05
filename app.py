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
    Форматирует содержимое Google Docs на основе префиксов для batchUpdate API.
    input_data — словарь с ключом 'content': массив параграфов,
      каждый из которых содержит 'text', 'startIndex' и 'endIndex'.

    Префиксы:
      <h1>: жирный, Lexend 16pt
      <h2>: жирный, Lexend 12pt
      <h3>: обычный, Lexend 11pt
      <h4>: обычный, Lexend 8pt
      <b1>: НЕ жирный, Lexend 8pt, ненумерованный список
      <b2>: НЕ жирный, Lexend 8pt, обычный текст

    Возвращает {'requests': ...} для передачи в Google Docs API.
    """
    styles = {
        '<h1>': {'fontSize': 16, 'bold': True,  'alignment': 'START', 'list': None},
        '<h2>': {'fontSize': 12, 'bold': True,  'alignment': 'START', 'list': None},
        '<h3>': {'fontSize': 11, 'bold': False, 'alignment': 'START', 'list': None},
        '<h4>': {'fontSize': 8,  'bold': False, 'alignment': 'START', 'list': None},
        '<b1>': {'fontSize': 8,  'bold': False, 'alignment': 'START', 'list': 'BULLET_DISC_CIRCLE_SQUARE'},
        '<b2>': {'fontSize': 8,  'bold': False, 'alignment': 'START', 'list': None}
    }

    requests = []
    content = input_data.get('content', [])

    for para in content:
        text = para.get('text', '')
        start = para.get('startIndex', 0)
        end = para.get('endIndex', 0)

        for prefix, style in styles.items():
            if text.startswith(prefix):
                prefix_len = len(prefix)
                # Удаляем префикс
                requests.append({
                    'deleteContentRange': {
                        'range': {
                            'startIndex': start,
                            'endIndex': start + prefix_len
                        }
                    }
                })
                text_start = start + prefix_len
                # Стиль текста
                requests.append({
                    'updateTextStyle': {
                        'range': {'startIndex': text_start, 'endIndex': end},
                        'textStyle': {
                            'bold': style['bold'],
                            'fontSize': {'magnitude': style['fontSize'], 'unit': 'PT'},
                            'weightedFontFamily': {'fontFamily': 'Lexend'}
                        },
                        'fields': 'bold,fontSize,weightedFontFamily'
                    }
                })
                # Стиль параграфа (выравнивание)
                requests.append({
                    'updateParagraphStyle': {
                        'range': {'startIndex': text_start, 'endIndex': end},
                        'paragraphStyle': {'alignment': style['alignment']},
                        'fields': 'alignment'
                    }
                })
                # Если это пункт списка — применяем bullets
                if style['list'] is not None:
                    requests.append({
                        'createParagraphBullets': {
                            'range': {'startIndex': text_start, 'endIndex': end},
                            'bulletPreset': style['list']
                        }
                    })
                break

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