from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from selection import ReviewSummary


APP_PATH = re.compile(r"^/app/([1-9][0-9]*)(?:/|$)")


def extract_app_id(href: str) -> int | None:
    if not isinstance(href, str) or not href:
        return None
    parsed = urlparse(href)
    if parsed.netloc and parsed.netloc.lower() != "store.steampowered.com":
        return None
    match = APP_PATH.match(parsed.path)
    return int(match.group(1)) if match else None


def parse_review_summary(payload: object) -> ReviewSummary | None:
    if not isinstance(payload, dict) or payload.get("success") != 1:
        return None
    query_summary = payload.get("query_summary")
    if not isinstance(query_summary, dict):
        return None
    total_positive = query_summary.get("total_positive")
    total_reviews = query_summary.get("total_reviews")
    if type(total_positive) is not int or type(total_reviews) is not int:
        return None
    if total_reviews <= 0 or not 0 <= total_positive <= total_reviews:
        return None
    return ReviewSummary(total_positive, total_reviews)


class SteamReviewCatalog:
    def __init__(self, session: Any, timeout: int = 30):
        self.session = session
        self.timeout = timeout
        self._cache: dict[int, ReviewSummary | None] = {}
        self.request_failures = 0
        self.invalid_responses = 0
        self.successful_responses = 0

    def get(self, app_id: int) -> ReviewSummary | None:
        if app_id in self._cache:
            return self._cache[app_id]

        try:
            response = self.session.get(
                f"https://store.steampowered.com/appreviews/{app_id}",
                params={
                    "json": 1,
                    "language": "all",
                    "purchase_type": "all",
                    "filter": "summary",
                },
                timeout=self.timeout,
            )
            summary = parse_review_summary(response.json())
            if summary is None:
                self.invalid_responses += 1
            else:
                self.successful_responses += 1
        except Exception:
            summary = None
            self.request_failures += 1

        self._cache[app_id] = summary
        return summary
