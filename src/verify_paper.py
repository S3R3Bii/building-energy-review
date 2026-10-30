"""Rebuild the manuscript's displayed values from observation-level exports."""

import csv, gzip, hashlib, json, statistics, unicodedata, re
from collections import Counter
from decimal import Decimal, localcontext
from fractions import Fraction as Q
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
read = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))


def csvrows(p):
    return list(csv.DictReader((ROOT / p).open(encoding="utf-8", newline="")))


def scalar(value):
    with localcontext() as c:
        c.prec = 50
        return float(
            (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
                Decimal("0.000000000001")
            )
        )


def measures(rows):
    n = len(rows)
    loss = [abs(Q(r["target_exact"]) - Q(r["prediction_exact"])) for r in rows]
    mae = sum(loss, Q(0)) / n
    mse = sum((x * x for x in loss), Q(0)) / n
    weighted = sum((x * Q(r["area_exact"]) for x, r in zip(loss, rows)), Q(0)) / sum(
        (Q(r["area_exact"]) for r in rows), Q(0)
    )
    with localcontext() as c:
        c.prec = 50
        rmse = float(
            (Decimal(mse.numerator) / Decimal(mse.denominator))
            .sqrt()
            .quantize(Decimal("0.000000000001"))
        )
    return dict(
        mae=scalar(mae), mse=scalar(mse), rmse=rmse, area_weighted_mae=scalar(weighted)
    )


def main():
    raw = json.loads(gzip.decompress((ROOT / "data/records.json.gz").read_bytes()))
    eligible = csvrows("results/source_eligibility.csv")
    dev = csvrows("results/development_predictions.csv")
    held = csvrows("results/held_predictions.csv")
    queue = csvrows("results/review_queue.csv")
    expected = {}
    for year in [2022, 2023, 2024]:
        expected[f"source-{year}-rows"] = (
            sum(int(r["report_year"]) == year for r in raw),
            "d",
            1,
        )
        expected[f"source-{year}-eligible"] = (
            sum(r["year"] == str(year) and int(r["eligible"]) for r in eligible),
            "d",
            1,
        )
    for config in [
        "pooled",
        "peer-w0",
        "peer-w1",
        "peer-w2",
        "peer-w3",
        "peer-w4",
        "own_lag",
    ]:
        m = measures([r for r in dev if r["config"] == config])
        for k in ["mae", "rmse"]:
            expected["dev-" + config + "-" + k] = (m[k], ".3f", 2)
    for config in ["peer-w0", "pooled", "own_lag"]:
        group = [r for r in held if r["config"] == config]
        m = measures(group)
        for k in ["mae", "mse", "rmse", "area_weighted_mae"]:
            expected["held-" + config + "-" + k] = (m[k], ".3f", 3)
        for t in ["Office", "Hotel", "Retail Store", "Non-Refrigerated Warehouse"]:
            expected["type-" + t + "-" + config] = (
                measures([r for r in group if r["type"] == t])["mae"],
                ".3f",
                3,
            )
    for i, (method, percent) in enumerate(
        (method, percent)
        for method in ["peer", "energy", "eui"]
        for percent in [5, 10, 20]
    ):
        group = [r for r in queue if int(r[method + "_selected_" + str(percent)])]
        valid = [r for r in group if int(r["followup_available"])]
        ratio = scalar(statistics.median(Q(r["followup_ratio_exact"]) for r in valid))
        parts = dict(
            selected=len(group),
            followup=len(valid),
            missing=len(group) - len(valid),
            ratio=ratio,
        )
        for k, value in parts.items():
            expected[f"capacity-{i}-{k}"] = (value, ".3f" if k == "ratio" else "d", 5)
        if percent == 10:
            for k, value in parts.items():
                expected["main-" + method + "-" + k] = (
                    value,
                    ".3f" if k == "ratio" else "d",
                    4,
                )
            expected["main-" + method + "-area"] = (
                float(statistics.median(Q(r["area_exact"]) for r in group)),
                ",.1f",
                4,
            )
            for j, band in enumerate([Q(5, 4), Q(3, 2), Q(2)]):
                expected[f"band-{method}-{j}-above"] = (
                    sum(Q(r["followup_ratio_exact"]) > band for r in valid),
                    "d",
                    5,
                )
                expected[f"band-{method}-{j}-denom"] = (len(valid), "d", 5)
    for i, (a, b) in enumerate(
        [("peer", "energy"), ("peer", "eui"), ("energy", "eui")]
    ):
        left = {r["building_key"] for r in queue if int(r[a + "_selected_10"])}
        right = {r["building_key"] for r in queue if int(r[b + "_selected_10"])}
        expected[f"overlap-{i}-intersection"] = (len(left & right), "d", 4)
        expected[f"overlap-{i}-union"] = (len(left | right), "d", 4)
    displayed = read("paper/TABLE_VALUES.json")
    assert set(displayed) == set(expected) and len(expected) == 119
    reader = PdfReader(ROOT / "paper/manuscript.pdf")
    assert len(reader.pages) == 7
    texts = [
        " ".join(
            re.sub(
                r"(?<=\w)-\s*\n\s*(?=\w)",
                "",
                unicodedata.normalize("NFKC", p.extract_text()),
            ).split()
        )
        for p in reader.pages
    ]
    joined = " ".join(texts)
    checks = 0
    for key, (number, fmt, page) in expected.items():
        assert displayed[key] == dict(value=number, text=format(number, fmt)), key
        assert format(number, fmt) in texts[page], key
        checks += 2
    for phrase in [
        "only 2 of 20",
        "41,062.5",
        "707,905.0",
        "nine remain unavailable",
        "no engineering review",
        "does not establish waste",
        "not the winning forecasting model",
        "192 buildings",
        "4,239 of 12,020",
        "5,008",
        "1,689",
        "original HTTP status",
        "145 validation pairs",
        "119 held pairs",
        "weight is zero",
        "21",
        "6b789b6",
        "22 tests",
        "103,262",
        "seven JSON documents",
        "31 source values",
        "248 SVG coordinate",
        "engineering impact still unmeasured",
        "Formal-publication projects",
    ]:
        assert phrase.casefold() in joined.casefold(), phrase
        checks += 1
    scope = read("paper/PUBLICATION_SCOPE.json")
    assert (
        scope["scope"] == "independent-program-study"
        and scope["formal_submission_target"] is False
        and len(scope["public_files"]) == 5
    )
    for name, digest in scope["public_files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        checks += 1
    assert all(len(t) > 800 for t in texts) and not reader.get_fields()
    root = reader.trailer["/Root"]
    assert "/AA" not in root and "/JavaScript" not in str(root.get("/Names", ""))
    if root.get("/OpenAction"):
        action = root["/OpenAction"].get_object()
        assert isinstance(action, list) and str(action[1]) == "/Fit"
    for page in reader.pages:
        assert "/AA" not in page
        box = page.mediabox
        for ref in page.get("/Annots", []):
            annotation = ref.get_object()
            assert "/AA" not in annotation
            action = annotation.get("/A")
            if action:
                assert action.get_object().get("/S") == "/URI"
            rect = annotation.get("/Rect")
            if rect:
                assert (
                    float(rect[0]) >= float(box.left) - 1
                    and float(rect[1]) >= float(box.bottom) - 1
                    and float(rect[2]) <= float(box.right) + 1
                    and float(rect[3]) <= float(box.top) + 1
                )
            checks += 1
    result = dict(
        status="PASS",
        checks=checks,
        pages=7,
        numeric_display_values=119,
        manuscript_sha256=hashlib.sha256(
            (ROOT / "paper/manuscript.pdf").read_bytes()
        ).hexdigest(),
        scope="Observation-export arithmetic,display text,claims,hashes and bounded PDF actions;visual checks recorded separately",
    )
    (ROOT / "results/paper_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
