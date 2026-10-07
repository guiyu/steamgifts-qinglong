import sys
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src" / "steam_gift"
sys.path.insert(0, str(SOURCE))

from reviews import SteamReviewCatalog, extract_app_id, parse_review_summary
from selection import ReviewSummary


class AppIdTests(unittest.TestCase):
    def test_extracts_app_id_from_absolute_or_relative_app_url(self):
        self.assertEqual(extract_app_id("https://store.steampowered.com/app/123"), 123)
        self.assertEqual(extract_app_id("/app/123?utm_source=SteamGifts"), 123)

    def test_rejects_malformed_and_unsupported_urls(self):
        for href in ("", "not a url", "/sub/123", "/app/nope", "/app/0"):
            with self.subTest(href=href):
                self.assertIsNone(extract_app_id(href))


class ReviewPayloadTests(unittest.TestCase):
    def test_parses_valid_summary(self):
        payload = {
            "success": 1,
            "query_summary": {"total_positive": 80, "total_reviews": 100},
        }

        self.assertEqual(parse_review_summary(payload), ReviewSummary(80, 100))

    def test_rejects_unsuccessful_missing_nonnumeric_or_invalid_summaries(self):
        invalid = (
            {"success": 0, "query_summary": {"total_positive": 80, "total_reviews": 100}},
            {"success": 1},
            {"success": 1, "query_summary": {"total_positive": "80", "total_reviews": 100}},
            {"success": 1, "query_summary": {"total_positive": 80, "total_reviews": "100"}},
            {"success": 1, "query_summary": {"total_positive": 0, "total_reviews": 0}},
            {"success": 1, "query_summary": {"total_positive": 101, "total_reviews": 100}},
            [],
        )

        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertIsNone(parse_review_summary(payload))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return FakeResponse(self.payload)


class CatalogTests(unittest.TestCase):
    def test_uses_public_summary_endpoint_and_caches_by_app_id(self):
        session = FakeSession(
            {"success": 1, "query_summary": {"total_positive": 90, "total_reviews": 100}}
        )
        catalog = SteamReviewCatalog(session, timeout=17)

        first = catalog.get(123)
        second = catalog.get(123)

        self.assertEqual(first, ReviewSummary(90, 100))
        self.assertIs(first, second)
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://store.steampowered.com/appreviews/123")
        self.assertEqual(
            kwargs["params"],
            {
                "json": 1,
                "language": "all",
                "purchase_type": "all",
                "filter": "summary",
            },
        )
        self.assertEqual(kwargs["timeout"], 17)

    def test_request_failure_is_a_cached_safe_skip(self):
        session = FakeSession(error=RuntimeError("offline"))
        catalog = SteamReviewCatalog(session)

        self.assertIsNone(catalog.get(456))
        self.assertIsNone(catalog.get(456))
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
