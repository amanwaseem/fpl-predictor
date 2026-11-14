"""Request bodies read their numbers as the schema says (#173).

Lax mode read a JSON true or false as 1 or 0 and "5" as 5. Schemathesis found
{"bank": false} answered 200. Checked on the models alone, with no database.
"""

import unittest

from pydantic import ValidationError

from fpl.api.schemas import OptimiseRequest

SQUAD = [{"player_id": i, "selling_price": 5.0} for i in range(1, 16)]


class TestOptimiseRequest(unittest.TestCase):
    def test_a_value_of_the_wrong_json_type_is_refused(self):
        for body in (
            {"bank": False},
            {"budget": True},
            {"free_transfers": "1"},
            {"gameweek": True},
            {"include_unavailable": 1},
            {"squad": [{"player_id": True, "selling_price": 5.0}, *SQUAD[1:]]},
            {"squad": [{"player_id": 1, "selling_price": "7.6"}, *SQUAD[1:]]},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                OptimiseRequest.model_validate(body)

    def test_numbers_are_read_as_json_schema_reads_them(self):
        """A whole number is a float, and 2.0 is an integer."""
        request = OptimiseRequest.model_validate(
            {"budget": 100, "free_transfers": 2.0, "gameweek": 8.0, "squad": SQUAD}
        )
        self.assertEqual((request.budget, request.free_transfers), (100.0, 2))
        self.assertEqual(request.gameweek, 8)


if __name__ == "__main__":
    unittest.main()
