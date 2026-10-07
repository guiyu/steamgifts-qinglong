import sys
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src" / "steam_gift"
sys.path.insert(0, str(SOURCE))

from selection import (
    Candidate,
    ReviewSummary,
    is_qualified,
    positive_percent,
    select_candidates,
)


class QualificationTests(unittest.TestCase):
    def test_accepts_exact_threshold(self):
        summary = ReviewSummary(total_positive=80, total_reviews=100)

        self.assertEqual(positive_percent(summary), 80)
        self.assertTrue(is_qualified(summary, min_percent=80, min_reviews=100))

    def test_rejects_below_positive_threshold(self):
        summary = ReviewSummary(total_positive=79, total_reviews=100)

        self.assertFalse(is_qualified(summary, min_percent=80, min_reviews=100))

    def test_rejects_below_review_threshold(self):
        summary = ReviewSummary(total_positive=80, total_reviews=99)

        self.assertFalse(is_qualified(summary, min_percent=80, min_reviews=100))

    def test_rejects_invalid_summaries(self):
        invalid = (
            ReviewSummary(total_positive=0, total_reviews=0),
            ReviewSummary(total_positive=-1, total_reviews=100),
            ReviewSummary(total_positive=80, total_reviews=-1),
            ReviewSummary(total_positive=101, total_reviews=100),
        )

        for summary in invalid:
            with self.subTest(summary=summary):
                self.assertFalse(
                    is_qualified(summary, min_percent=80, min_reviews=100)
                )

    def test_candidate_rejects_non_positive_point_cost(self):
        review = ReviewSummary(total_positive=80, total_reviews=100)

        for points in (0, -1):
            with self.subTest(points=points):
                with self.assertRaises(ValueError):
                    Candidate(
                        code="abcde",
                        url="https://www.steamgifts.com/giveaway/abcde/example",
                        points=points,
                        app_id=123,
                        review=review,
                    )


class AllocationTests(unittest.TestCase):
    def candidate(
        self,
        code,
        points,
        percent=90,
        reviews=100,
        app_id=None,
    ):
        positive = percent * reviews // 100
        return Candidate(
            code=code,
            url=f"https://www.steamgifts.com/giveaway/{code}/example",
            points=points,
            app_id=app_id or sum(ord(character) for character in code),
            review=ReviewSummary(positive, reviews),
        )

    def test_selects_exact_spend_and_returns_descending_points(self):
        candidates = [
            self.candidate("four", 4),
            self.candidate("nine", 9),
            self.candidate("six", 6),
        ]

        selected = select_candidates(candidates, budget=10)

        self.assertEqual([candidate.code for candidate in selected], ["six", "four"])

    def test_selects_best_under_budget(self):
        candidates = [self.candidate("five", 5), self.candidate("seven", 7)]

        selected = select_candidates(candidates, budget=8)

        self.assertEqual([candidate.code for candidate in selected], ["seven"])

    def test_deduplicates_giveaway_codes_before_allocation(self):
        candidates = [
            self.candidate("same", 4, app_id=1),
            self.candidate("same", 6, app_id=2),
            self.candidate("other", 6, app_id=3),
        ]

        selected = select_candidates(candidates, budget=10)

        self.assertEqual([candidate.code for candidate in selected], ["other", "same"])
        self.assertEqual(next(item for item in selected if item.code == "same").points, 4)

    def test_ignores_candidates_above_budget(self):
        selected = select_candidates([self.candidate("large", 11)], budget=10)

        self.assertEqual(selected, [])

    def test_equal_spend_prefers_higher_minimum_positive_percent(self):
        candidates = [
            self.candidate("a", 5, percent=90),
            self.candidate("b", 5, percent=80),
            self.candidate("c", 6, percent=85),
            self.candidate("d", 4, percent=85),
        ]

        selected = select_candidates(candidates, budget=10)

        self.assertEqual({candidate.code for candidate in selected}, {"c", "d"})

    def test_equal_quality_prefers_higher_total_review_count(self):
        candidates = [
            self.candidate("a", 6, percent=90, reviews=100),
            self.candidate("b", 4, percent=90, reviews=100),
            self.candidate("c", 5, percent=90, reviews=1000),
            self.candidate("d", 5, percent=90, reviews=100),
        ]

        selected = select_candidates(candidates, budget=10)

        self.assertEqual({candidate.code for candidate in selected}, {"c", "d"})

    def test_final_tie_break_uses_stable_code_order_without_mutating_input(self):
        candidates = [self.candidate("zzzzz", 10), self.candidate("aaaaa", 10)]
        original = list(candidates)

        selected = select_candidates(candidates, budget=10)

        self.assertEqual([candidate.code for candidate in selected], ["aaaaa"])
        self.assertEqual(candidates, original)


if __name__ == "__main__":
    unittest.main()
