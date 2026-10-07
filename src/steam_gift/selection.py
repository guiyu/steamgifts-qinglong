from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ReviewSummary:
    total_positive: int
    total_reviews: int


@dataclass(frozen=True)
class Candidate:
    code: str
    url: str
    points: int
    app_id: int
    review: ReviewSummary

    def __post_init__(self) -> None:
        if not self.code or not self.url or self.points <= 0 or self.app_id <= 0:
            raise ValueError("candidate fields must be non-empty and positive")
        if not isinstance(self.review, ReviewSummary):
            raise ValueError("candidate review must be a ReviewSummary")


def _valid_summary(summary: ReviewSummary) -> bool:
    return (
        isinstance(summary.total_positive, int)
        and isinstance(summary.total_reviews, int)
        and summary.total_reviews > 0
        and 0 <= summary.total_positive <= summary.total_reviews
    )


def positive_percent(summary: ReviewSummary) -> int:
    if not _valid_summary(summary):
        return 0
    return summary.total_positive * 100 // summary.total_reviews


def is_qualified(
    summary: ReviewSummary,
    min_percent: int,
    min_reviews: int,
) -> bool:
    return (
        _valid_summary(summary)
        and summary.total_reviews >= min_reviews
        and positive_percent(summary) >= min_percent
    )


def _prefer(left: tuple[Candidate, ...], right: tuple[Candidate, ...]) -> bool:
    left_minimum = min(positive_percent(item.review) for item in left)
    right_minimum = min(positive_percent(item.review) for item in right)
    if left_minimum != right_minimum:
        return left_minimum > right_minimum

    left_reviews = sum(item.review.total_reviews for item in left)
    right_reviews = sum(item.review.total_reviews for item in right)
    if left_reviews != right_reviews:
        return left_reviews > right_reviews

    return tuple(sorted(item.code for item in left)) < tuple(
        sorted(item.code for item in right)
    )


def select_candidates(
    candidates: Sequence[Candidate],
    budget: int,
) -> list[Candidate]:
    if budget <= 0:
        return []

    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.code, candidate)

    states: dict[int, tuple[Candidate, ...]] = {0: ()}
    for candidate in sorted(unique.values(), key=lambda item: item.code):
        for spent, selected in sorted(states.items(), reverse=True):
            new_spent = spent + candidate.points
            if new_spent > budget:
                continue
            proposed = selected + (candidate,)
            existing = states.get(new_spent)
            if existing is None or _prefer(proposed, existing):
                states[new_spent] = proposed

    best_spend = max(states)
    return sorted(states[best_spend], key=lambda item: (-item.points, item.code))
