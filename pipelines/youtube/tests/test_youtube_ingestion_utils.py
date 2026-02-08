import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_ingestion_service import extract_comments, parse_duration


class YoutubeIngestionUtilsTests(unittest.TestCase):
    def test_parse_duration_with_hours(self):
        self.assertEqual(parse_duration("PT1H2M3S"), "1:02:03")

    def test_parse_duration_with_minutes_only(self):
        self.assertEqual(parse_duration("PT7M5S"), "7:05")

    def test_parse_duration_invalid(self):
        self.assertEqual(parse_duration("invalid"), "0:00")

    def test_extract_comments(self):
        items = [
            {
                "snippet": {
                    "topLevelComment": {
                        "snippet": {
                            "textDisplay": "hello",
                            "textOriginal": "hello original",
                        }
                    }
                }
            }
        ]
        self.assertEqual(
            extract_comments(items),
            [{"textDisplay": "hello", "textOriginal": "hello original"}],
        )


if __name__ == "__main__":
    unittest.main()
