import sys, unittest
from fractions import Fraction as F
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model import (
    parse,
    median,
    area_bin,
    decimal,
    fit,
    predict,
    CONFIGS,
    LAG,
    metrics,
    pair_reason,
    qualify,
)


def training(n=20):
    return [
        dict(eui=F(i + 1), type="Office", area=F(50000), building_key=str(i))
        for i in range(n)
    ]


class Fixtures(unittest.TestCase):
    def test_odd_median(self):
        self.assertEqual(median([F(100), F(2), F(3)]), 3)

    def test_even_median(self):
        self.assertEqual(median([F(1), F(2)]), F(3, 2))

    def test_missing_is_not_zero(self):
        for value in [
            "",
            None,
            "Not Available",
            "Unable to Check (not enough data)",
            "NaN",
            "inf",
        ]:
            self.assertIsNone(parse(value))
        self.assertEqual(parse("0"), 0)

    def test_bin_edges(self):
        self.assertEqual(
            [
                area_bin(F(x))
                for x in [24999, 25000, 49999, 50000, 100000, 5000000, 5000001]
            ],
            [None, 0, 0, 1, 2, 5, None],
        )

    def test_round_ties_even(self):
        self.assertEqual(decimal(F(1, 2000000000000)), "0.000000000000")
        self.assertEqual(decimal(F(3, 2000000000000)), "0.000000000002")

    def test_type_fallback(self):
        fitted = fit(training())
        r = dict(type="Hotel", area=F(50000), eui=F(100))
        prediction, why = predict(r, CONFIGS[-1], fitted)
        self.assertEqual(prediction, F(21, 2))
        self.assertEqual(why, "pooled_fallback")

    def test_bin_fallback(self):
        fitted = fit(training())
        prediction, why = predict(
            dict(type="Office", area=F(25000)), CONFIGS[-1], fitted
        )
        self.assertEqual(prediction, F(21, 2))
        self.assertEqual(why, "type_fallback")

    def test_shrinkage(self):
        rows = training() + [
            dict(
                eui=F(100 + i), type="Office", area=F(250000), building_key="b" + str(i)
            )
            for i in range(20)
        ]
        fitted = fit(rows)
        prediction, _ = predict(dict(type="Office", area=F(50000)), CONFIGS[3], fitted)
        self.assertEqual(
            prediction, (F(fitted["types"]["Office"]["median"]) + F("10.5")) / 2
        )

    def test_persistence(self):
        self.assertEqual(predict(dict(eui=F(17)), LAG, {}), (F(17), "own_prior_eui"))

    def test_exact_metrics(self):
        m = metrics(
            [
                dict(target=F(10), prediction=F(12), area=F(1)),
                dict(target=F(20), prediction=F(16), area=F(3)),
            ]
        )
        self.assertEqual(m["mae_exact"], "3")
        self.assertEqual(m["mse_exact"], "10")
        self.assertEqual(m["area_weighted_mae_exact"], "7/2")

    def test_missing_followup(self):
        self.assertEqual(
            pair_reason(dict(year=2023, building_key="a"), {}, set()),
            "no_target_in_captured_source",
        )

    def test_invalid_followup(self):
        self.assertEqual(
            pair_reason(dict(year=2023, building_key="a"), {}, {(2024, "a")}),
            "invalid_or_ambiguous_target",
        )

    def test_type_change(self):
        self.assertEqual(
            pair_reason(
                dict(year=2023, building_key="a", type="Office"),
                {(2024, "a"): dict(type="Hotel")},
                set(),
            ),
            "type_changed",
        )

    def test_area_change_and_boundary(self):
        current = dict(year=2023, building_key="a", type="Office", area=F(100))
        future = dict(type="Office", area=F(125))
        self.assertEqual(pair_reason(current, {(2024, "a"): future}, set()), "")
        future["area"] = F(126)
        self.assertEqual(
            pair_reason(current, {(2024, "a"): future}, set()), "area_changed"
        )

    def test_same_capacity_ties(self):
        rows = [
            dict(key="b", score=F(2)),
            dict(key="a", score=F(2)),
            dict(key="c", score=F(1)),
        ]
        self.assertEqual(
            [r["key"] for r in sorted(rows, key=lambda r: (-r["score"], r["key"]))[:2]],
            ["a", "b"],
        )
        self.assertEqual((21 * 10 + 99) // 100, 3)


if __name__ == "__main__":
    unittest.main()
