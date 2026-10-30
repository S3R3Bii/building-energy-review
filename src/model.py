"""Exact median peer references and explicit source/pair eligibility."""

import gzip, json, re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPES = ["Office", "Hotel", "Retail Store", "Non-Refrigerated Warehouse"]
EDGES = [25000, 50000, 100000, 250000, 500000, 1000000, 5000001]
ALERTS = [
    "alert_energy_meter_has_less",
    "alert_energy_meter_has_gaps",
    "alert_energy_meter_has",
    "alert_energy_no_meters",
]
CONFIGS = [
    dict(id="pooled", kind="pooled", alpha="0"),
    *[dict(id="peer-w" + str(i), kind="peer", alpha=str(F(i, 4))) for i in range(5)],
]
LAG = dict(id="own_lag", kind="lag", alpha="0")


def parse(value):
    try:
        s = str(value).strip()
        return F(s) if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s) else None
    except (ValueError, ZeroDivisionError):
        return None


def decimal(value):
    with localcontext() as ctx:
        ctx.prec = 50
        rounded = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
            Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN
        )
        return format(rounded, ".12f")


def median(values):
    values = sorted(values)
    assert values
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def area_bin(area):
    return next(
        (i for i, (a, b) in enumerate(zip(EDGES, EDGES[1:])) if a <= area < b), None
    )


def load_rows():
    return json.loads(gzip.decompress((ROOT / "data/records.json.gz").read_bytes()))


def qualify(rows):
    counts = Counter((r["report_year"], r["building_key"]) for r in rows)
    links = defaultdict(set)
    for r in rows:
        if r["property_valid"] and r["bin_valid"]:
            links[r["property_key"]].add(r["building_key"])
    eligible = []
    audit = []
    for r in rows:
        reasons = []
        year = parse(r["report_year"])
        area = parse(r["property_gfa_self_reported"])
        calculated = parse(r["property_gfa_calculated_1"])
        eui = parse(r["weather_normalized_site_eui"])
        site = parse(r["site_eui_kbtu_ft"])
        energy = parse(r["site_energy_use_kbtu"])
        occupancy = parse(r["occupancy"])
        buildings = parse(r["number_of_buildings"])
        if year not in [2022, 2023, 2024]:
            reasons.append("year")
        if year is None or r["year_ending"][:10] != str(int(year)) + "-12-31":
            reasons.append("period_end")
        if not r["bin_valid"]:
            reasons.append("invalid_bin")
        if not r["property_valid"]:
            reasons.append("invalid_property_id")
        if counts[(r["report_year"], r["building_key"])] > 1:
            reasons.append("ambiguous_year_bin")
        if len(links[r["property_key"]]) > 1:
            reasons.append("property_maps_multiple_bins")
        if r["primary_property_type"] not in TYPES:
            reasons.append("type")
        if r["parent_property_id"] != "Not Applicable: Standalone Property":
            reasons.append("campus_or_unknown_parent")
        if r["construction_status"] != "Existing":
            reasons.append("construction")
        if buildings != 1:
            reasons.append("building_count")
        if occupancy is None or not 80 <= occupancy <= 100:
            reasons.append("occupancy")
        if area is None or not 25000 <= area <= 5000000:
            reasons.append("area")
        if (
            calculated is None
            or calculated <= 0
            or area is None
            or area <= 0
            or abs(calculated - area) > area / 20
        ):
            reasons.append("area_disagreement")
        if eui is None or not 0 < eui <= 2000:
            reasons.append("weather_eui")
        if site is None or site <= 0:
            reasons.append("site_eui")
        if energy is None or energy <= 0:
            reasons.append("site_energy")
        if (
            site is not None
            and site > 0
            and energy is not None
            and energy > 0
            and area is not None
            and area > 0
        ):
            if abs(site - energy / area) > max(F(1, 5), site / 100):
                reasons.append("site_energy_area_identity")
        if r["estimated_values_energy"] != "No":
            reasons.append("estimated_or_unknown_energy")
        for field in ALERTS:
            if r[field] != "Ok":
                reasons.append(field)
        audit.append(
            dict(
                source_row=r["source_row"],
                year=r["report_year"],
                building_key=r["building_key"],
                type=r["primary_property_type"],
                eligible=int(not reasons),
                reasons=";".join(reasons),
            )
        )
        if not reasons:
            eligible.append(
                dict(
                    building_key=r["building_key"],
                    bucket=r["bucket"],
                    year=int(year),
                    type=r["primary_property_type"],
                    area=area,
                    eui=eui,
                    site_eui=site,
                    site_energy=energy,
                    source_row=r["source_row"],
                )
            )
    assert len({(r["year"], r["building_key"]) for r in eligible}) == len(eligible)
    return eligible, audit


def pair_reason(current, lookup, raw_keys):
    year = current["year"] + 1
    key = (year, current["building_key"])
    following = lookup.get(key)
    if following is None:
        return (
            "invalid_or_ambiguous_target"
            if key in raw_keys
            else "no_target_in_captured_source"
        )
    if following["type"] != current["type"]:
        return "type_changed"
    if not F(4, 5) <= following["area"] / current["area"] <= F(5, 4):
        return "area_changed"
    return ""


def prepare():
    rows = load_rows()
    eligible, audit = qualify(rows)
    lookup = {(r["year"], r["building_key"]): r for r in eligible}
    raw_keys = {(int(r["report_year"]), r["building_key"]) for r in rows}
    train = [r for r in eligible if r["year"] == 2022 and r["bucket"] in [0, 1, 2]]
    pairs = {}
    pair_audit = []
    assert len(train) >= 100, "Fixed training coverage hold"
    for stage, year, bucket in [("development", 2022, 3), ("held", 2023, 4)]:
        cases = []
        for r in eligible:
            if r["year"] != year or r["bucket"] != bucket:
                continue
            reason = pair_reason(r, lookup, raw_keys)
            pair_audit.append(
                dict(
                    stage=stage,
                    building_key=r["building_key"],
                    input_source_row=r["source_row"],
                    paired=int(not reason),
                    reason=reason,
                )
            )
            if not reason:
                cases.append(
                    dict(
                        r,
                        target=lookup[(year + 1, r["building_key"])]["eui"],
                        target_source_row=lookup[(year + 1, r["building_key"])][
                            "source_row"
                        ],
                    )
                )
        assert len(cases) >= 50, "Fixed pair coverage hold: " + stage
        pairs[stage] = cases
    assert not (
        {r["building_key"] for r in train}
        & {r["building_key"] for r in pairs["development"]}
        or {r["building_key"] for r in train}
        & {r["building_key"] for r in pairs["held"]}
        or {r["building_key"] for r in pairs["development"]}
        & {r["building_key"] for r in pairs["held"]}
    )
    return dict(
        rows=rows,
        eligible=eligible,
        audit=audit,
        lookup=lookup,
        raw_keys=raw_keys,
        train=train,
        pairs=pairs,
        pair_audit=pair_audit,
    )


def fit(train):
    pooled = median([r["eui"] for r in train])
    types = {}
    bins = {}
    for t in TYPES:
        values = [r["eui"] for r in train if r["type"] == t]
        types[t] = dict(
            n=len(values),
            median=str(median(values) if len(values) >= 20 else pooled),
            fallback=len(values) < 20,
        )
        for i in range(6):
            values = [
                r["eui"] for r in train if r["type"] == t and area_bin(r["area"]) == i
            ]
            bins[t + "|" + str(i)] = dict(
                n=len(values),
                median=str(
                    median(values) if len(values) >= 20 else F(types[t]["median"])
                ),
                fallback=len(values) < 20,
            )
    return dict(
        pooled=str(pooled),
        types=types,
        bins=bins,
        train_n=len(train),
        training_keys=sorted(r["building_key"] for r in train),
    )


def predict(row, config, fitted):
    if config["kind"] == "lag":
        return row["eui"], "own_prior_eui"
    if config["kind"] == "pooled":
        return F(fitted["pooled"]), "pooled"
    t = fitted["types"][row["type"]]
    b = fitted["bins"][row["type"] + "|" + str(area_bin(row["area"]))]
    a = F(config["alpha"])
    return (1 - a) * F(t["median"]) + a * F(b["median"]), (
        "pooled_fallback"
        if t["fallback"]
        else "type_fallback" if b["fallback"] else "type_area"
    )


def metrics(rows):
    n = len(rows)
    absolute = [abs(r["target"] - r["prediction"]) for r in rows]
    squared = [(r["target"] - r["prediction"]) ** 2 for r in rows]
    mae = sum(absolute, F(0)) / n
    mse = sum(squared, F(0)) / n
    weighted = sum((loss * r["area"] for loss, r in zip(absolute, rows)), F(0)) / sum(
        (r["area"] for r in rows), F(0)
    )
    with localcontext() as ctx:
        ctx.prec = 50
        rmse = format(
            (Decimal(mse.numerator) / Decimal(mse.denominator))
            .sqrt()
            .quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN), ".12f"
        )
    return dict(
        n=n,
        mae=decimal(mae),
        mae_exact=str(mae),
        mse=decimal(mse),
        mse_exact=str(mse),
        rmse=rmse,
        area_weighted_mae=decimal(weighted),
        area_weighted_mae_exact=str(weighted),
    )
