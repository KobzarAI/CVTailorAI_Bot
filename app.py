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
