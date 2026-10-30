"""Independent source eligibility, SQL pair lineage and exact arithmetic checks.

No production model, eligibility or scoring helper is imported.
"""

import argparse, csv, gzip, hashlib, json, re, sqlite3, statistics
from collections import Counter, defaultdict
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction as Q
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPES = ["Office", "Hotel", "Retail Store", "Non-Refrigerated Warehouse"]
EDGES = [25000, 50000, 100000, 250000, 500000, 1000000, 5000001]


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def csvrows(name):
    return list(csv.DictReader((ROOT / name).open(encoding="utf-8", newline="")))


def number(value):
    s = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
        return None
    return Q(Decimal(s))


def dec(value):
    with localcontext() as c:
        c.prec = 50
        return format(
            (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
                Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN
            ),
            ".12f",
        )


def independent_source():
    raw = json.loads(gzip.decompress((ROOT / "data/records.json.gz").read_bytes()))
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE raw(i INT,y TEXT,b TEXT,p TEXT,bucket INT,bvalid INT,pvalid INT)"
    )
    db.executemany(
        "INSERT INTO raw VALUES (?,?,?,?,?,?,?)",
        [
            (
                r["source_row"],
                r["report_year"],
                r["building_key"],
                r["property_key"],
                r["bucket"],
                r["bin_valid"],
                r["property_valid"],
            )
            for r in raw
        ],
    )
    duplicate = {
        (y, b)
        for y, b, n in db.execute(
            "SELECT y,b,COUNT(*) FROM raw GROUP BY y,b HAVING COUNT(*)>1"
        )
    }
    bad_properties = {
        p
        for p, n in db.execute(
            "SELECT p,COUNT(DISTINCT b) FROM raw WHERE bvalid=1 AND pvalid=1 GROUP BY p HAVING COUNT(DISTINCT b)>1"
        )
    }
    audit = {int(r["source_row"]): r for r in csvrows("results/source_eligibility.csv")}
    eligible = {}
    checks = 0
    for r in raw:
        n = {
            k: number(r[k])
            for k in [
                "report_year",
                "number_of_buildings",
                "occupancy",
                "property_gfa_self_reported",
                "property_gfa_calculated_1",
                "weather_normalized_site_eui",
                "site_eui_kbtu_ft",
                "site_energy_use_kbtu",
            ]
        }
        yr = n["report_year"]
        a = n["property_gfa_self_reported"]
        ca = n["property_gfa_calculated_1"]
        w = n["weather_normalized_site_eui"]
        se = n["site_eui_kbtu_ft"]
        en = n["site_energy_use_kbtu"]
        oc = n["occupancy"]
        bad = []
        checks_by_reason = {
            "year": yr in (2022, 2023, 2024),
            "period_end": yr is not None
            and r["year_ending"].startswith(str(int(yr)) + "-12-31"),
            "invalid_bin": r["bin_valid"],
            "invalid_property_id": r["property_valid"],
            "ambiguous_year_bin": (r["report_year"], r["building_key"])
            not in duplicate,
            "property_maps_multiple_bins": r["property_key"] not in bad_properties,
            "type": r["primary_property_type"] in TYPES,
            "campus_or_unknown_parent": r["parent_property_id"]
            == "Not Applicable: Standalone Property",
            "construction": r["construction_status"] == "Existing",
            "building_count": n["number_of_buildings"] == 1,
            "occupancy": oc is not None and 80 <= oc <= 100,
            "area": a is not None and 25000 <= a <= 5000000,
            "area_disagreement": a is not None
            and a > 0
            and ca is not None
            and ca > 0
            and Q(95, 100) <= ca / a <= Q(105, 100),
            "weather_eui": w is not None and 0 < w <= 2000,
            "site_eui": se is not None and se > 0,
            "site_energy": en is not None and en > 0,
            "estimated_or_unknown_energy": r["estimated_values_energy"] == "No",
        }
        if (
            a is not None
            and a > 0
            and se is not None
            and se > 0
            and en is not None
            and en > 0
        ):
            checks_by_reason["site_energy_area_identity"] = abs(se - en / a) <= max(
                Q("0.2"), Q(".01") * se
            )
        for key in [
            "alert_energy_meter_has_less",
            "alert_energy_meter_has_gaps",
            "alert_energy_meter_has",
            "alert_energy_no_meters",
        ]:
            checks_by_reason[key] = r[key] == "Ok"
        bad = {k for k, v in checks_by_reason.items() if not v}
        got = audit[r["source_row"]]
        assert set(filter(None, got["reasons"].split(";"))) == bad
        assert int(got["eligible"]) == int(not bad)
        checks += len(checks_by_reason) + 2
        if not bad:
            eligible[(int(yr), r["building_key"])] = dict(
                year=int(yr),
                key=r["building_key"],
                type=r["primary_property_type"],
                bucket=r["bucket"],
                row=r["source_row"],
                area=a,
                eui=w,
                energy=en,
            )
    db.execute("CREATE TABLE eligible(y INT,b TEXT,bucket INT,i INT,t TEXT,a TEXT)")
    db.executemany(
        "INSERT INTO eligible VALUES (?,?,?,?,?,?)",
        [
            (r["year"], r["key"], r["bucket"], r["row"], r["type"], str(r["area"]))
            for r in eligible.values()
        ],
    )
    db.create_function(
        "stable_area", 2, lambda a, b: int(Q(4, 5) <= Q(b) / Q(a) <= Q(5, 4))
    )
    return raw, eligible, db, checks


def independently_fit(train):
    pooled = statistics.median(r["eui"] for r in train)
    types = {}
    bins = {}
    for t in TYPES:
        group = [r["eui"] for r in train if r["type"] == t]
        types[t] = dict(
            n=len(group),
            median=str(statistics.median(group) if len(group) >= 20 else pooled),
            fallback=len(group) < 20,
        )
        for i, (lo, hi) in enumerate(zip(EDGES, EDGES[1:])):
            group = [r["eui"] for r in train if r["type"] == t and lo <= r["area"] < hi]
            bins[t + "|" + str(i)] = dict(
                n=len(group),
                median=str(
                    statistics.median(group)
                    if len(group) >= 20
                    else Q(types[t]["median"])
                ),
                fallback=len(group) < 20,
            )
    return dict(
        pooled=str(pooled),
        types=types,
        bins=bins,
        train_n=len(train),
        training_keys=sorted(r["key"] for r in train),
    )


def peer(r, config, fitted):
    if config == "own_lag":
        return r["eui"]
    if config == "pooled":
        return Q(fitted["pooled"])
    weight = Q(int(config.removeprefix("peer-w")), 4)
    index = next(
        i for i, (a, b) in enumerate(zip(EDGES, EDGES[1:])) if a <= r["area"] < b
    )
    t = Q(fitted["types"][r["type"]]["median"])
    v = Q(fitted["bins"][r["type"] + "|" + str(index)]["median"])
    return t + weight * (v - t)


def group_metrics(rows):
    n = len(rows)
    losses = [Q(r["absolute_loss_exact"]) for r in rows]
    mae = sum(losses, Q(0)) / n
    mse = sum((Q(r["squared_loss_exact"]) for r in rows), Q(0)) / n
    weighted = sum((x * Q(r["area_exact"]) for x, r in zip(losses, rows)), Q(0)) / sum(
        (Q(r["area_exact"]) for r in rows), Q(0)
    )
    with localcontext() as c:
        c.prec = 50
        rmse = format(
            (Decimal(mse.numerator) / Decimal(mse.denominator))
            .sqrt()
            .quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN),
            ".12f",
        )
    return dict(
        n=n,
        mae=dec(mae),
        mae_exact=str(mae),
        mse=dec(mse),
        mse_exact=str(mse),
        rmse=rmse,
        area_weighted_mae=dec(weighted),
        area_weighted_mae_exact=str(weighted),
    )


def verify(stage):
    raw, eligible, db, checks = independent_source()
    train = [
        eligible[(y, b)]
        for y, b in db.execute(
            "SELECT y,b FROM eligible WHERE y=2022 AND bucket IN(0,1,2)"
        )
    ]
    fitted = independently_fit(train)
    assert fitted == read("results/fitted_peers.json")
    checks += len(fitted["bins"]) * 3 + len(fitted["types"]) * 3 + 3
    year, bucket = (2022, 3) if stage == "development" else (2023, 4)
    expected = {
        b: (i, j)
        for b, i, j in db.execute(
            "SELECT a.b,a.i,z.i FROM eligible a JOIN eligible z ON z.b=a.b AND z.y=a.y+1 WHERE a.y=? AND a.bucket=? AND a.t=z.t AND stable_area(a.a,z.a)=1",
            (year, bucket),
        )
    }
    assert len(expected) >= 50
    pred = csvrows("results/" + stage + "_predictions.csv")
    configs = sorted({r["config"] for r in pred})
    assert len(pred) == len(configs) * len(expected)
    train_keys = {r["key"] for r in train}
    dev_keys = {
        r[0] for r in db.execute("SELECT b FROM eligible WHERE y=2022 AND bucket=3")
    }
    held_keys = {
        r[0] for r in db.execute("SELECT b FROM eligible WHERE y=2023 AND bucket=4")
    }
    assert (
        not train_keys & dev_keys
        and not train_keys & held_keys
        and not dev_keys & held_keys
    )
    for config in configs:
        assert {r["building_key"] for r in pred if r["config"] == config} == set(
            expected
        )
    for r in pred:
        current = eligible[(year, r["building_key"])]
        target = eligible[(year + 1, r["building_key"])]
        prediction = peer(current, r["config"], fitted)
        loss = abs(target["eui"] - prediction)
        assert (int(r["input_source_row"]), int(r["target_source_row"])) == expected[
            r["building_key"]
        ]
        assert (r["type"], int(r["input_year"]), int(r["target_year"])) == (
            current["type"],
            year,
            year + 1,
        )
        for key, value in [
            ("area_exact", current["area"]),
            ("current_eui_exact", current["eui"]),
            ("target_exact", target["eui"]),
            ("prediction_exact", prediction),
            ("absolute_loss_exact", loss),
            ("squared_loss_exact", loss * loss),
        ]:
            assert Q(r[key]) == value
            checks += 1
        for key, value in [
            ("target", target["eui"]),
            ("prediction", prediction),
            ("absolute_loss", loss),
        ]:
            assert r[key] == dec(value)
            checks += 1
        checks += 5
    summaries = read("results/" + stage + "_metrics.json")
    for s in summaries:
        group = [r for r in pred if r["config"] == s["config"]]
        assert s["overall"] == group_metrics(group)
        checks += 8
        for t, m in s["by_type"].items():
            assert m == group_metrics([r for r in group if r["type"] == t])
            checks += 8
    if stage == "development":
        ids = ["pooled", "peer-w0", "peer-w1", "peer-w2", "peer-w3", "peer-w4"]
        scores = {s["config"]: Q(s["overall"]["mae_exact"]) for s in summaries}
        winner = min(enumerate(ids), key=lambda x: (scores[x[1]], x[0]))[1]
        assert read("results/selection.json")["config"]["id"] == winner
        assert set(configs) == set(ids + ["own_lag"])
        checks += 2
    else:
        for name, digest in read("results/frozen_dependencies.json")["sha256"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            checks += 1
        checks += verify_queue(raw, eligible, fitted)
    result = dict(
        status="PASS",
        stage=stage,
        checks=checks,
        cases=len(expected),
        prediction_rows=len(pred),
        source_rule_and_SQL_lineage="Independent decimal/rational rules,SQLite duplicate/identity/year/pair joins and exact medians/losses",
        scope="Current-source retrospective benchmark; no engineering usefulness,original-vintage or savings validation",
    )
    (ROOT / "results" / ("independent_" + stage + ".json")).write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


def verify_queue(raw, eligible, fitted):
    selected = read("results/selection.json")["config"]["id"]
    queue = [r for r in eligible.values() if r["year"] == 2023 and r["bucket"] == 4]
    actual = csvrows("results/review_queue.csv")
    by_key = {r["building_key"]: r for r in actual}
    assert set(by_key) == {r["key"] for r in queue}
    checks = 1
    raw_keys = {(int(r["report_year"]), r["building_key"]) for r in raw}
    scores = {}
    rankings = {}
    members = {}
    for r in queue:
        scores[r["key"]] = dict(
            peer=r["eui"] / peer(r, selected, fitted), energy=r["energy"], eui=r["eui"]
        )
    for method in ["peer", "energy", "eui"]:
        order = sorted(queue, key=lambda r: (-scores[r["key"]][method], r["key"]))
        rankings[method] = {r["key"]: i for i, r in enumerate(order, 1)}
        for percent in [5, 10, 20]:
            members[(method, percent)] = {
                r["key"] for r in order[: max(1, (len(queue) * percent + 99) // 100)]
            }
    for r in queue:
        got = by_key[r["key"]]
        future = eligible.get((2024, r["key"]))
        reason = ""
        if future is None:
            reason = (
                "invalid_or_ambiguous_target"
                if (2024, r["key"]) in raw_keys
                else "no_target_in_captured_source"
            )
        elif future["type"] != r["type"]:
            reason = "type_changed"
        elif not Q(4, 5) <= future["area"] / r["area"] <= Q(5, 4):
            reason = "area_changed"
        assert got["followup_reason"] == reason and int(
            got["followup_available"]
        ) == int(not reason)
        assert Q(got["peer_reference_exact"]) == peer(r, selected, fitted)
        assert Q(got["peer_ratio_exact"]) == scores[r["key"]]["peer"]
        checks += 4
        if not reason:
            assert Q(got["followup_eui_exact"]) == future["eui"] and Q(
                got["followup_ratio_exact"]
            ) == future["eui"] / peer(r, selected, fitted)
            checks += 2
        for method in rankings:
            assert int(got[method + "_rank"]) == rankings[method][r["key"]]
            checks += 1
            for percent in [5, 10, 20]:
                assert int(got[method + "_selected_" + str(percent)]) == int(
                    r["key"] in members[(method, percent)]
                )
                checks += 1
    for s in read("results/review_metrics.json"):
        subset = [
            r
            for r in actual
            if int(r[s["method"] + "_selected_" + str(s["capacity_percent"])])
        ]
        valid = [
            Q(r["followup_ratio_exact"]) for r in subset if int(r["followup_available"])
        ]
        assert (s["queue_n"], s["selected_n"], s["followup_n"], s["unavailable_n"]) == (
            len(queue),
            len(subset),
            len(valid),
            len(subset) - len(valid),
        )
        assert s["median_area"] == dec(
            statistics.median(Q(r["area_exact"]) for r in subset)
        )
        assert s["median_followup_ratio"] == (
            dec(statistics.median(valid)) if valid else None
        )
        assert s["type_counts"] == dict(Counter(r["type"] for r in subset))
        assert s["unavailable_reasons"] == dict(
            Counter(r["followup_reason"] for r in subset if r["followup_reason"])
        )
        checks += 8
        for band in s["bands"]:
            assert (band["above"], band["denominator"]) == (
                sum(x > Q(band["ratio"]) for x in valid),
                len(valid),
            )
            checks += 2
    for s in read("results/review_overlaps.json"):
        a = members[(s["first"], s["capacity_percent"])]
        b = members[(s["second"], s["capacity_percent"])]
        assert (s["intersection"], s["union"], s["selected_n"], s["jaccard_exact"]) == (
            len(a & b),
            len(a | b),
            len(a),
            str(Q(len(a & b), len(a | b))),
        )
        checks += 4
    return checks


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["development", "held"])
    verify(p.parse_args().stage)
