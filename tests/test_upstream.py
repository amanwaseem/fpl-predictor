"""What the advice route reconstructs from the public FPL API, fpl.api.upstream (#83)."""

import unittest

from fpl.api import upstream


def history(*transfers):
    """entry/{id}/history/'s `current`, from GW1, with these transfers each gameweek."""
    return [{"event": gw, "event_transfers": n} for gw, n in enumerate(transfers, start=1)]


class TestFreeTransfers(unittest.TestCase):

    def test_one_after_the_first_gameweek(self):
        self.assertEqual(upstream.free_transfers(history(9), [], through=1), 1)

    def test_an_unused_one_is_banked(self):
        self.assertEqual(upstream.free_transfers(history(0, 0), [], through=2), 2)

    def test_banked_up_to_five(self):
        self.assertEqual(upstream.free_transfers(history(*[0] * 10), [], through=10), 5)

    def test_used_ones_are_gone_and_a_hit_leaves_none_banked(self):
        self.assertEqual(upstream.free_transfers(history(0, 0, 1), [], through=3), 2)
        self.assertEqual(upstream.free_transfers(history(0, 3), [], through=2), 1)

    def test_a_wildcard_or_free_hit_uses_none(self):
        for chip in ("wildcard", "freehit"):
            with self.subTest(chip=chip):
                chips = [{"name": chip, "event": 2}]
                self.assertEqual(upstream.free_transfers(history(0, 12), chips, through=2), 2)

    def test_other_chips_do_not_change_the_count(self):
        chips = [{"name": "bboost", "event": 2}]
        self.assertEqual(upstream.free_transfers(history(0, 1), chips, through=2), 1)

    def test_only_gameweeks_up_to_through_count(self):
        self.assertEqual(upstream.free_transfers(history(0, 0, 5), [], through=2), 2)

    def test_a_late_starter_counts_from_their_first_gameweek(self):
        late = [{"event": 5, "event_transfers": 0}, {"event": 6, "event_transfers": 0}]
        self.assertEqual(upstream.free_transfers(late, [], through=6), 2)


class TestSellingPrice(unittest.TestCase):

    def test_half_of_a_rise_rounded_down(self):
        self.assertEqual(upstream.selling_price(60, 63), 61)
        self.assertEqual(upstream.selling_price(60, 64), 62)

    def test_all_of_a_fall(self):
        self.assertEqual(upstream.selling_price(60, 57), 57)


class TestPurchasePrices(unittest.TestCase):

    def transfer(self, element, cost, event, time):
        return {"element_in": element, "element_in_cost": cost, "event": event, "time": time}

    def test_the_latest_purchase_counts(self):
        transfers = [self.transfer(7, 50, 2, "2026-08-20"), self.transfer(7, 55, 4, "2026-09-10")]
        self.assertEqual(upstream.purchase_prices({7}, transfers, [], through=5), {7: 55})

    def test_a_free_hit_purchase_is_undone(self):
        transfers = [self.transfer(7, 50, 2, "2026-08-20"), self.transfer(7, 55, 4, "2026-09-10")]
        chips = [{"name": "freehit", "event": 4}]
        self.assertEqual(upstream.purchase_prices({7}, transfers, chips, through=5), {7: 50})

    def test_players_not_in_the_squad_and_later_gameweeks_are_ignored(self):
        transfers = [self.transfer(8, 50, 2, "2026-08-20"), self.transfer(7, 55, 6, "2026-09-30")]
        self.assertEqual(upstream.purchase_prices({7}, transfers, [], through=5), {})


if __name__ == "__main__":
    unittest.main()
