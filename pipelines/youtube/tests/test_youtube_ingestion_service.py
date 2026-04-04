import sys
from pathlib import Path
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_ingestion_service import build_quota_estimate, run_ingestion


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeUpdateResult:
    def __init__(self, upserted_id=None, modified_count=0):
        self.upserted_id = upserted_id
        self.modified_count = modified_count


class _FakeCollection:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def update_one(self, flt, update_doc, upsert=False):
        self.calls.append((flt, update_doc, upsert))
        return self._results.pop(0)


class YoutubeIngestionServiceTests(unittest.TestCase):
    def test_build_quota_estimate_warning_level(self):
        estimate = build_quota_estimate(
            skills_count=70,
            search_max_results=5,
            daily_limit=10000,
            warning_threshold=7500,
        )
        self.assertEqual(estimate["estimated_units"], 7350)
        self.assertEqual(estimate["level"], "ok")

    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_insert_summary(self, mock_request_json):
        def _side_effect(url, params):
            if "search" in url:
                return _FakeResponse(200), {
                    "items": [
                        {
                            "id": {"videoId": "abc123"},
                            "snippet": {
                                "publishedAt": "2024-01-01T00:00:00Z",
                                "title": "Sample Title",
                                "description": "Sample Description",
                            },
                        }
                    ]
                }
            if "videos" in url:
                return _FakeResponse(200), {
                    "items": [
                        {
                            "statistics": {"viewCount": "10", "likeCount": "2", "commentCount": "5"},
                            "snippet": {"tags": ["a", "b"]},
                            "contentDetails": {"duration": "PT1M2S"},
                        }
                    ]
                }
            raise AssertionError("Unexpected URL")

        mock_request_json.side_effect = _side_effect
        collection = _FakeCollection([_FakeUpdateResult(upserted_id="new-id")])

        summary = run_ingestion(
            collection,
            sector="Accountancy",
            api_key="key",
            search_max_results=5,
            search_order="relevance",
            selected_skills=["Skill A"],
            competency="",
            proficiency="",
            additional_query="",
        )

        self.assertEqual(summary["skills_processed"], 1)
        self.assertEqual(summary["videos_found"], 1)
        self.assertEqual(summary["upserts_attempted"], 1)
        self.assertEqual(summary["inserted"], 1)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(summary["unchanged"], 0)
        self.assertEqual(summary["error_count"], 0)

        self.assertEqual(len(collection.calls), 1)
        flt, update_doc, upsert = collection.calls[0]
        self.assertEqual(flt, {"videoId": "abc123", "skill_name": "Skill A"})
        self.assertTrue(upsert)
        doc = update_doc["$set"]
        self.assertEqual(doc["duration"], "1:02")
        self.assertEqual(doc["viewCount"], 10)
        self.assertEqual(doc["likeCount"], 2)
        self.assertEqual(doc["commentCount"], 5)

    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_filters_by_min_counts(self, mock_request_json):
        def _side_effect(url, params):
            if "search" in url:
                return _FakeResponse(200), {
                    "items": [
                        {
                            "id": {"videoId": "abc123"},
                            "snippet": {
                                "publishedAt": "2024-01-01T00:00:00Z",
                                "title": "Sample Title",
                                "description": "Sample Description",
                            },
                        }
                    ]
                }
            if "videos" in url:
                return _FakeResponse(200), {
                    "items": [
                        {
                            "statistics": {"viewCount": "10", "likeCount": "2", "commentCount": "1"},
                            "snippet": {"tags": ["a"]},
                            "contentDetails": {"duration": "PT1M2S"},
                        }
                    ]
                }
            if "commentThreads" in url:
                return _FakeResponse(200), {"items": []}
            raise AssertionError("Unexpected URL")

        mock_request_json.side_effect = _side_effect
        collection = _FakeCollection([])

        summary = run_ingestion(
            collection,
            sector="Accountancy",
            api_key="key",
            search_max_results=5,
            search_order="relevance",
            selected_skills=["Skill A"],
            competency="",
            proficiency="",
            min_view_count=1000,
            min_like_count=5,
            additional_query="",
        )

        self.assertEqual(summary["videos_filtered_constraints"], 1)
        self.assertEqual(summary["upserts_attempted"], 0)
        self.assertEqual(len(collection.calls), 0)

    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_handles_search_http_error(self, mock_request_json):
        mock_request_json.return_value = _FakeResponse(403), {"error": {"reason": "quotaExceeded"}}
        collection = _FakeCollection([])

        summary = run_ingestion(
            collection,
            sector="Accountancy",
            api_key="key",
            search_max_results=10,
            search_order="relevance",
            selected_skills=["Skill A"],
            competency="",
            proficiency="",
            additional_query="",
        )

        self.assertEqual(summary["skills_processed"], 1)
        self.assertEqual(summary["upserts_attempted"], 0)
        self.assertEqual(summary["error_count"], 1)
        self.assertIn("HTTP 403", summary["errors"][0])

    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_with_additional_query(self, mock_request_json):
        def _side_effect(url, params):
            if "search" in url:
                # With new LLM logic, fallback query is different:
                # {skill} {keywordize(comp)} {base_terms} {negatives} {additional}
                # since comp is empty, it becomes: "Skill A  (tutorial|...)" with an extra space or just cleaned by filter(None)
                
                # Check for key components loosely
                q = params["q"]
                self.assertIn("Skill A", q)
                self.assertIn("tutorial", q)
                # Ensure our hardcoded negatives are there
                self.assertIn("-shorts", q)
                
                return _FakeResponse(200), {
    
    # ...existing code...
    
                    "items": [{
                        "id": {"videoId": "video1"},
                        "snippet": {
                            "publishedAt": "2023-01-01T00:00:00Z",
                            "title": "Test Video",
                            "description": "Test Description",
                        }
                    }]
                }
            elif "videos" in url:
                return _FakeResponse(200), {
                    "items": [{
                        "statistics": {
                            "viewCount": "1000",
                            "likeCount": "10",
                            "commentCount": "5",
                        },
                        "contentDetails": {
                            "duration": "PT10M",
                        }
                    }]
                }
            else:
                raise AssertionError("Unexpected URL")

        mock_request_json.side_effect = _side_effect
        collection = _FakeCollection([_FakeUpdateResult(upserted_id="new-id")])

        summary = run_ingestion(
            collection,
            sector="Accountancy",
            api_key="key",
            search_max_results=5,
            search_order="relevance",
            selected_skills=["Skill A"],
            competency="",
            proficiency="",
            additional_query="tutorial",
        )

        self.assertEqual(summary["skills_processed"], 1)
        self.assertEqual(summary["videos_found"], 1)
        self.assertEqual(summary["upserts_attempted"], 1)
        self.assertEqual(summary["constraints"]["additional_query"], "tutorial")

    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_with_max_video_age(self, mock_request_json):
        from datetime import datetime, timedelta
        
        def _side_effect(url, params):
            if "search" in url:
                # Verify the query includes publishedAfter parameter
                self.assertIn("publishedAfter", params)
                # Check that the date is approximately 30 days ago
                expected_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                actual_date = params["publishedAfter"][:10]  # Extract date part
                self.assertEqual(actual_date, expected_date)
                return _FakeResponse(200), {
                    "items": [{
                        "id": {"videoId": "video1"},
                        "snippet": {
                            "publishedAt": "2023-01-01T00:00:00Z",
                            "title": "Test Video",
                            "description": "Test Description",
                        }
                    }]
                }
            elif "videos" in url:
                return _FakeResponse(200), {
                    "items": [{
                        "statistics": {
                            "viewCount": "1000",
                            "likeCount": "10",
                            "commentCount": "5",
                        },
                        "contentDetails": {
                            "duration": "PT10M",
                        }
                    }]
                }
            else:
                raise AssertionError("Unexpected URL")

        mock_request_json.side_effect = _side_effect
        collection = _FakeCollection([_FakeUpdateResult(upserted_id="new-id")])

        summary = run_ingestion(
            collection,
            sector="Accountancy",
            api_key="key",
            search_max_results=5,
            search_order="relevance",
            selected_skills=["Skill A"],
            competency="",
            proficiency="",
            max_video_age=30,
        )

        self.assertEqual(summary["skills_processed"], 1)
        self.assertEqual(summary["videos_found"], 1)
        self.assertEqual(summary["upserts_attempted"], 1)
        self.assertEqual(summary["constraints"]["max_video_age"], 30)

    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_with_competency(self, mock_request_json):
        def _side_effect(url, params):
            if "search" in url:
                # With Q3 only: skill + keywordize(comp) + keywordize(prof) + base + negatives + additional
                # Skill A + (accounting financial) + (accounting principles apply basic) + base + negatives + advanced tutorial
                q = params["q"]
                self.assertTrue(q.startswith("Skill A"))
                self.assertIn("accounting", q)
                self.assertIn("financial", q)
                self.assertIn("advanced tutorial", q)
                
                return _FakeResponse(200), {
                    "items": [{
                        "id": {"videoId": "video1"},
                        "snippet": {
                            "publishedAt": "2023-01-01T00:00:00Z",
                            "title": "Test Video",
                            "description": "Test Description",
                        }
                    }]
                }
            elif "videos" in url:
                return _FakeResponse(200), {
                    "items": [{
                        "statistics": {
                            "viewCount": "1000",
                            "likeCount": "10",
                            "commentCount": "5",
                        },
                        "contentDetails": {
                            "duration": "PT10M",
                        }
                    }]
                }
            else:
                raise AssertionError("Unexpected URL")

        mock_request_json.side_effect = _side_effect
        collection = _FakeCollection([_FakeUpdateResult(upserted_id="new-id")])

        # Mock get_proficiency_description
        with patch("youtube_ingestion_service.get_proficiency_description") as mock_get_proficiency_description:
            mock_get_proficiency_description.return_value = "Apply basic accounting principles"

            summary = run_ingestion(
                collection,
                sector="Accountancy",
                api_key="key",
                search_max_results=5,
                search_order="relevance",
                selected_skills=["Skill A"],
                competency="Financial Accounting",
                proficiency="Intermediate",
                additional_query="advanced tutorial",
            )

        self.assertEqual(summary["skills_processed"], 1)
        self.assertEqual(summary["videos_found"], 1)
        self.assertEqual(summary["upserts_attempted"], 1)
        self.assertEqual(summary["constraints"]["proficiency"], "Intermediate")
        self.assertEqual(summary["constraints"]["competency"], "Financial Accounting")
        # Updated assertion: Q3 logic does not use exact string concatenation anymore but 'keywordize' + base terms
        # Just check it starts with Skill A and contains the additional query
        q = summary["constraints"]["query"]
        self.assertTrue(q.startswith("Skill A"))
        self.assertTrue(q.endswith("advanced tutorial"))

    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_with_query_include_flags(self, mock_request_json):
        def _side_effect(url, params):
            if "search" in url:
                # With Q3 only: "Skill A" + keywordize(comp, 3) + base + negatives + additional
                # competency="Financial Accounting", requirement="Apply basic accounting principles"
                # Query should contain: Skill A, accounting, financial, tutorial
                # It should NOT contain "principles" (from requirement which is removed)
                q = params["q"]
                self.assertIn("Skill A", q)
                self.assertIn("accounting", q)
                self.assertIn("financial", q)
                self.assertIn("tutorial", q)
                
                # Verify requirement words are NOT in query if they are unique to requirement
                # "principles" is in requirement but not competency
                self.assertNotIn("principles", q)

                return _FakeResponse(200), {
                    "items": [{
                        "id": {"videoId": "video1"},
                        "snippet": {
                            "publishedAt": "2023-01-01T00:00:00Z",
                            "title": "Test Video",
                            "description": "Test Description",
                        }
                    }]
                }
            elif "videos" in url:
                return _FakeResponse(200), {
                    "items": [{
                        "statistics": {
                            "viewCount": "1000",
                            "likeCount": "10",
                            "commentCount": "5",
                        },
                        "contentDetails": {
                            "duration": "PT10M",
                        }
                    }]
                }
            else:
                raise AssertionError("Unexpected URL")

        mock_request_json.side_effect = _side_effect
        collection = _FakeCollection([_FakeUpdateResult(upserted_id="new-id")])

        summary = run_ingestion(
            collection,
            sector="Accountancy",
            api_key="key",
            search_max_results=5,
            search_order="relevance",
            selected_skills=["Skill A"],
            competency="Financial Accounting",
            proficiency="",
            requirement="Apply basic accounting principles",
            additional_query="tutorial",
            query_includes={
                "sector": False,
                "skill": True,
                "competency": False,
                "requirement": True,
            },
        )

        self.assertEqual(summary["skills_processed"], 1)
        self.assertEqual(summary["videos_found"], 1)
        self.assertEqual(summary["upserts_attempted"], 1)
        
        q = summary["constraints"]["query"]
        self.assertIn("Skill A", q)
        self.assertIn("accounting", q)
        self.assertIn("financial", q)
        self.assertIn("tutorial", q)
        self.assertNotIn("principles", q)


if __name__ == "__main__":
    unittest.main()
