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
                # Verify the query includes sector, skill, and additional query
                self.assertEqual(params["q"], "Accountancy Skill A tutorial")
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

    @patch("youtube_ingestion_service.get_requirement")
    @patch("youtube_ingestion_service.request_json")
    def test_run_ingestion_with_proficiency_description(self, mock_request_json, mock_get_requirement):
        # Mock the proficiency requirements
        mock_get_requirement.return_value = ["Knowledge: Basic accounting principles", "Skill: Financial statement analysis"]
        
        def _side_effect(url, params):
            if "search" in url:
                # Verify the query includes sector, skill, proficiency description, and additional query
                expected_query = "Accountancy Skill A Knowledge: Basic accounting principles Skill: Financial statement analysis advanced tutorial"
                self.assertEqual(params["q"], expected_query)
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
            proficiency="Intermediate",
            additional_query="advanced tutorial",
        )

        self.assertEqual(summary["skills_processed"], 1)
        self.assertEqual(summary["videos_found"], 1)
        self.assertEqual(summary["upserts_attempted"], 1)
        self.assertEqual(summary["constraints"]["proficiency"], "Intermediate")
        mock_get_requirement.assert_called_once_with("Skill A", "Financial Accounting", "Intermediate")


if __name__ == "__main__":
    unittest.main()
