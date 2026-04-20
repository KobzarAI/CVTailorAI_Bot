import copy
import os
import time
import unittest
from unittest.mock import patch

import app as fastapi_app_module
import resume_utils
from fastapi.testclient import TestClient
from resume_utils import (
    add_new_bullet,
    compute_ats_metrics,
    cv2text,
    filter_and_rank_bullets,
    find_gaps_and_update_master,
    normalize_master_resume,
)


def build_master_resume():
    return {
        "personal_info": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "location": "Kyiv",
            "linkedin": "linkedin.com/in/jane",
            "portfolio": "janedoe.dev",
        },
        "desired_positions": ["Data Engineer"],
        "skills": {
            "hard_skills": [
                {"term": "Python", "confirmed_by": [], "origin": True},
                {"term": "SQL", "confirmed_by": [], "origin": True},
            ],
            "soft_skills": [
                {"term": "Leadership", "confirmed_by": [], "origin": True},
            ],
        },
        "keywords": [
            {"term": "Automation", "confirmed_by": [], "origin": True},
        ],
        "experience": [
            {
                "company": "Acme Corp",
                "job_title": "Data Engineer",
                "location": "Remote",
                "start_date": "Jan 2020",
                "end_date": "Jan 2022",
                "bullets": [
                    {
                        "id": 10,
                        "text": "Built ETL pipelines for reporting.",
                        "skills_used": ["Python"],
                        "keyword_used": ["Automation"],
                    },
                    {
                        "id": 11,
                        "text": "Built ETL pipelines for reporting.",
                        "skills_used": ["Python"],
                        "keyword_used": ["Automation"],
                    },
                    {
                        "id": 12,
                        "text": "Led SQL migration across legacy systems.",
                        "skills_used": ["SQL", "Domain Modeling"],
                        "keyword_used": ["Stakeholder Management"],
                    },
                ],
            }
        ],
        "education": [],
        "certifications": [],
        "languages": [{"language": "English", "proficiency": "C1"}],
        "unconfirmed": {"skills": [], "keywords": []},
        "explicitly_not_used": {"skills": [], "keywords": []},
    }


def build_extract():
    return {
        "job_title": "Senior Data Engineer",
        "required_skills": [
            {"term": "Python", "synonyms": ["Py"], "type": "hard", "priority": 1},
            {"term": "SQL", "synonyms": [], "type": "hard", "priority": 2},
        ],
        "required_keywords": [
            {"term": "Automation", "synonyms": ["Automated"], "priority": 3},
        ],
        "mandatory": {
            "skills": ["Python"],
            "keywords": ["Automation"],
        },
        "nice_to_have": {
            "skills": ["SQL"],
            "keywords": [],
        },
    }


class CodeIntegrityTests(unittest.TestCase):
    def test_expected_routes_are_registered(self):
        paths = {route.path for route in fastapi_app_module.app.routes}
        expected_paths = {
            "/merge",
            "/format_google_doc",
            "/find_gaps",
            "/generate_adapted_resume",
            "/unconfirmed_to_terms",
            "/btnsCompany",
            "/select_to_confirm_list",
            "/auto_confirm",
            "/remove_duplicates",
            "/normalize_master",
            "/cv_to_text",
            "/push_bullets",
            "/ats_score",
            "/analyze_job",
            "/skills2master",
            "/bullets2buttons",
            "/term_not_used",
            "/get_company_bullets",
            "/confirm_term",
            "/add_new_bullet",
        }
        self.assertTrue(expected_paths.issubset(paths))

    def test_normalize_master_resume_restores_core_invariants(self):
        normalized = normalize_master_resume(copy.deepcopy(build_master_resume()))

        bullets = normalized["experience"][0]["bullets"]
        bullet_ids = [bullet["id"] for bullet in bullets]

        self.assertEqual(bullet_ids, [1, 2])
        self.assertEqual(normalized["personal_info"]["linkedin"], "https://linkedin.com/in/jane")
        self.assertEqual(normalized["personal_info"]["portfolio"], "https://janedoe.dev")
        self.assertEqual(normalized["experience"][0]["duration_years"], 2.0)

        python_skill = next(
            item for item in normalized["skills"]["hard_skills"] if item["term"] == "Python"
        )
        sql_skill = next(
            item for item in normalized["skills"]["hard_skills"] if item["term"] == "SQL"
        )

        self.assertEqual(python_skill["confirmed_by"], [1])
        self.assertEqual(sql_skill["confirmed_by"], [2])
        self.assertIn("Leadership", normalized["unconfirmed"]["skills"])
        self.assertIn("Domain Modeling", normalized["unknown"]["skills"])

        keyword_terms = {item["term"] for item in normalized["keywords"]}
        self.assertIn("Automation", keyword_terms)
        self.assertIn("Stakeholder Management", keyword_terms)

    def test_find_gaps_adds_only_missing_non_blocked_terms(self):
        master_resume = normalize_master_resume(copy.deepcopy(build_master_resume()))
        master_resume["explicitly_not_used"]["skills"].append("Airflow")

        extract = {
            "required_skills": [
                {"term": "Python", "synonyms": [], "type": "hard"},
                {"term": "Airflow", "synonyms": [], "type": "hard"},
                {"term": "dbt", "synonyms": ["data build tool"], "type": "hard"},
            ],
            "required_keywords": [
                {"term": "Automation", "synonyms": []},
                {"term": "Warehousing", "synonyms": ["Data Warehouse"]},
            ],
        }

        updated = find_gaps_and_update_master(extract, master_resume)

        self.assertIn("dbt", updated["unconfirmed"]["skills"])
        self.assertIn("Warehousing", updated["unconfirmed"]["keywords"])
        self.assertNotIn("Airflow", updated["unconfirmed"]["skills"])

    def test_filter_and_rank_bullets_keeps_required_terms(self):
        normalized = normalize_master_resume(copy.deepcopy(build_master_resume()))
        adapted = filter_and_rank_bullets(normalized, build_extract())

        self.assertEqual(adapted["desired_positions"], ["Senior Data Engineer"])

        selected_bullets = [
            bullet
            for experience in adapted["experience"]
            for bullet in experience.get("bullets", [])
        ]
        self.assertGreaterEqual(len(selected_bullets), 1)

        selected_terms = set()
        for bullet in selected_bullets:
            combined_terms = bullet.get("skills_used", []) + bullet.get("keyword_used", [])
            self.assertLessEqual(len(combined_terms), 3)
            selected_terms.update(combined_terms)

        self.assertIn("Python", selected_terms)
        self.assertIn("Automation", selected_terms)

    def test_cv2text_builds_expected_markup(self):
        normalized = normalize_master_resume(copy.deepcopy(build_master_resume()))
        rendered = cv2text(normalized)

        self.assertIn("[[h1]]Jane Doe", rendered)
        self.assertIn("[[h2]]Work experience", rendered)
        self.assertIn("[[b1]]Built ETL pipelines for reporting.", rendered)
        self.assertIn("https://linkedin.com/in/jane", rendered)

    def test_ats_metrics_falls_back_to_tfidf_when_semantic_api_unavailable(self):
        with patch.object(resume_utils, "get_semantic_similarity", return_value=0.0):
            metrics = compute_ats_metrics(
                "Python SQL automation pipelines and warehousing",
                "Python automation reporting and SQL dashboards",
            )

        self.assertIn("ats_score(70-90)", metrics)
        self.assertIn("semantic(coverage_incl_synonyms_0.6-0.85)", metrics)
        self.assertGreater(metrics["semantic(coverage_incl_synonyms_0.6-0.85)"], 0.0)
        self.assertIn("python", metrics["overlap_keywords"])
        self.assertIn("sql", metrics["overlap_keywords"])

    def test_add_new_bullet_preserves_bidirectional_links(self):
        normalized = normalize_master_resume(copy.deepcopy(build_master_resume()))
        updated = add_new_bullet(
            master_json=normalized,
            company="Acme Corp",
            bullet="Mentored analysts and coordinated delivery across teams.",
            term_name="Leadership",
            term_type="soft",
        )

        bullets = updated["experience"][0]["bullets"]
        new_bullet = bullets[-1]
        leadership_skill = next(
            item for item in updated["skills"]["soft_skills"] if item["term"] == "Leadership"
        )

        self.assertEqual(new_bullet["skills_used"], ["Leadership"])
        self.assertEqual(leadership_skill["confirmed_by"], [new_bullet["id"]])
        self.assertNotIn("Leadership", updated["unconfirmed"]["skills"])


class ApiPriorityOneTests(unittest.TestCase):
    def setUp(self):
        fastapi_app_module.rate_limiter.reset()
        self.client = TestClient(fastapi_app_module.app)
        self.headers = {"X-API-Key": "test-key"}

    def test_health_endpoint_is_public(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_missing_api_key_is_rejected(self):
        with patch.dict(os.environ, {"API_KEY": "test-key"}, clear=False):
            response = self.client.post("/cv_to_text", json=build_master_resume())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid or missing API key.")

    def test_validation_errors_return_structured_422(self):
        with patch.dict(os.environ, {"API_KEY": "test-key"}, clear=False):
            response = self.client.post(
                "/generate_adapted_resume",
                headers=self.headers,
                json={"extended_master_resume": build_master_resume()},
            )

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["detail"], "Request validation failed.")
        self.assertTrue(any("extract" in str(item["loc"]) for item in body["errors"]))

    def test_request_body_limit_returns_413(self):
        oversized_body = {
            "job_text": "x" * 500,
            "resume_text": "y" * 500,
        }

        with patch.dict(
            os.environ,
            {"API_KEY": "test-key", "MAX_REQUEST_BODY_BYTES": "200"},
            clear=False,
        ):
            response = self.client.post("/ats_score", headers=self.headers, json=oversized_body)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error_code"], "request_too_large")

    def test_rate_limit_returns_429(self):
        with patch.dict(
            os.environ,
            {
                "API_KEY": "test-key",
                "RATE_LIMIT_REQUESTS": "1",
                "RATE_LIMIT_WINDOW_SECONDS": "60",
            },
            clear=False,
        ):
            first = self.client.post("/cv_to_text", headers=self.headers, json=build_master_resume())
            second = self.client.post("/cv_to_text", headers=self.headers, json=build_master_resume())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error_code"], "rate_limit_exceeded")

    def test_unhandled_errors_are_sanitized(self):
        with patch.dict(os.environ, {"API_KEY": "test-key"}, clear=False):
            with patch.object(fastapi_app_module, "compute_ats_metrics", side_effect=RuntimeError("secret token")):
                response = self.client.post(
                    "/ats_score",
                    headers=self.headers,
                    json={"job_text": "python sql", "resume_text": "python sql"},
                )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Internal server error.")
        self.assertNotIn("secret token", response.text)

    def test_request_timeout_returns_504(self):
        def slow_compute(job_text, resume_text):
            time.sleep(0.05)
            return {
                "ats_score(70-90)": 50.0,
                "semantic(coverage_incl_synonyms_0.6-0.85)": 0.5,
                "recall(JD->CV_0.6-0.85)": 0.5,
                "precision(density_of_terms_0.4-0.7)": 0.5,
                "overlap_keywords": ["python"],
                "job_keywords": ["python"],
                "resume_keywords": ["python"],
            }

        with patch.dict(
            os.environ,
            {"API_KEY": "test-key", "REQUEST_TIMEOUT_SECONDS": "0.01"},
            clear=False,
        ):
            with patch.object(fastapi_app_module, "compute_ats_metrics", side_effect=slow_compute):
                response = self.client.post(
                    "/ats_score",
                    headers=self.headers,
                    json={"job_text": "python", "resume_text": "python"},
                )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error_code"], "request_timeout")


if __name__ == "__main__":
    unittest.main()
