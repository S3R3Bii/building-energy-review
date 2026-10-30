"""Separate development selection from frozen held outcomes and review queues."""

import argparse, csv, hashlib, json
from collections import Counter
from fractions import Fraction as F
from pathlib import Path
from model import (
    ROOT,
    TYPES,
    CONFIGS,
    LAG,
    prepare,
    fit,
    predict,
    metrics,
    decimal,
    median,
    pair_reason,
)


def dump(name, value):
    p = ROOT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def csvout(name, rows):
    assert rows
    path = ROOT / name
    path.parent.mkdir(exist_ok=True, parents=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def evaluate(stage, cases, configs, fitted):
    exported = []
    summary = []
    for config in configs:
        scored = []
        for case in sorted(cases, key=lambda r: r["building_key"]):
            prediction, level = predict(case, config, fitted)
            scored.append(dict(case, prediction=prediction))
            loss = abs(case["target"] - prediction)
            exported.append(
                dict(
                    config=config["id"],
                    building_key=case["building_key"],
                    type=case["type"],
                    input_year=case["year"],
                    target_year=case["year"] + 1,
                    input_source_row=case["source_row"],
                    target_source_row=case["target_source_row"],
                    area_exact=str(case["area"]),
                    current_eui_exact=str(case["eui"]),
                    target_exact=str(case["target"]),
                    prediction_exact=str(prediction),
                    absolute_loss_exact=str(loss),
                    squared_loss_exact=str(loss**2),
                    target=decimal(case["target"]),
                    prediction=decimal(prediction),
                    absolute_loss=decimal(loss),
                    fallback=level,
                )
            )
        summary.append(
            dict(
                config=config["id"],
                overall=metrics(scored),
                by_type={
                    t: metrics([r for r in scored if r["type"] == t])
                    for t in TYPES
                    if any(r["type"] == t for r in scored)
                },
            )
        )
    csvout("results/" + stage + "_predictions.csv", exported)
    dump("results/" + stage + "_metrics.json", summary)
    return summary


def review_queues(data, selected, fitted):
    queue = [r for r in data["eligible"] if r["year"] == 2023 and r["bucket"] == 4]
    assert queue
    scores = {}
    predictions = {}
    for r in queue:
        prediction, _ = predict(r, selected, fitted)
        predictions[r["building_key"]] = prediction
        scores[r["building_key"]] = dict(
            peer=r["eui"] / prediction, energy=r["site_energy"], eui=r["eui"]
        )
    ordered = {
        method: sorted(
            queue, key=lambda r: (-scores[r["building_key"]][method], r["building_key"])
        )
        for method in ["peer", "energy", "eui"]
    }
    ranks = {
        method: {r["building_key"]: i for i, r in enumerate(values, 1)}
        for method, values in ordered.items()
    }
    members = {}
    summaries = []
    rows = []
    for method in ordered:
        for percent in [5, 10, 20]:
            k = max(1, (len(queue) * percent + 99) // 100)
            chosen = ordered[method][:k]
            valid = []
            missing = Counter()
            for r in chosen:
                reason = pair_reason(r, data["lookup"], data["raw_keys"])
                if reason:
                    missing[reason] += 1
                else:
                    valid.append(
                        data["lookup"][(2024, r["building_key"])]["eui"]
                        / predictions[r["building_key"]]
                    )
            members[(method, percent)] = {r["building_key"] for r in chosen}
            summaries.append(
                dict(
                    method=method,
                    capacity_percent=percent,
                    queue_n=len(queue),
                    selected_n=k,
                    followup_n=len(valid),
                    unavailable_n=k - len(valid),
                    unavailable_reasons=dict(missing),
                    median_area=decimal(median([r["area"] for r in chosen])),
                    type_counts=dict(Counter(r["type"] for r in chosen)),
                    median_followup_ratio=decimal(median(valid)) if valid else None,
                    bands=[
                        dict(
                            ratio=str(band),
                            above=sum(v > band for v in valid),
                            denominator=len(valid),
                        )
                        for band in [F(5, 4), F(3, 2), F(2)]
                    ],
                )
            )
    for r in sorted(queue, key=lambda r: r["building_key"]):
        prediction = predictions[r["building_key"]]
        reason = pair_reason(r, data["lookup"], data["raw_keys"])
        future = (
            data["lookup"][(2024, r["building_key"])]["eui"] if not reason else None
        )
        rows.append(
            dict(
                building_key=r["building_key"],
                type=r["type"],
                input_source_row=r["source_row"],
                area_exact=str(r["area"]),
                eui_exact=str(r["eui"]),
                site_energy_exact=str(r["site_energy"]),
                peer_reference_exact=str(prediction),
                peer_ratio_exact=str(scores[r["building_key"]]["peer"]),
                peer_rank=ranks["peer"][r["building_key"]],
                energy_rank=ranks["energy"][r["building_key"]],
                eui_rank=ranks["eui"][r["building_key"]],
                followup_available=int(not reason),
                followup_reason=reason,
                followup_eui_exact=str(future) if future is not None else "",
                followup_ratio_exact=(
                    str(future / prediction) if future is not None else ""
                ),
                **{
                    method
                    + "_selected_"
                    + str(percent): int(r["building_key"] in members[(method, percent)])
                    for method in ordered
                    for percent in [5, 10, 20]
                }
            )
        )
    overlaps = []
    for percent in [5, 10, 20]:
        for first, second in [("peer", "energy"), ("peer", "eui"), ("energy", "eui")]:
            a = members[(first, percent)]
            b = members[(second, percent)]
            overlaps.append(
                dict(
                    capacity_percent=percent,
                    first=first,
                    second=second,
                    intersection=len(a & b),
                    union=len(a | b),
                    selected_n=len(a),
                    jaccard_exact=str(F(len(a & b), len(a | b))),
                )
            )
    csvout("results/review_queue.csv", rows)
    dump("results/review_metrics.json", summaries)
    dump("results/review_overlaps.json", overlaps)
    return dict(
        queue_n=len(queue),
        main=[r for r in summaries if r["capacity_percent"] == 10],
        main_overlap=[r for r in overlaps if r["capacity_percent"] == 10],
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["development", "held"])
    a = p.parse_args()
    data = prepare()
    if a.stage == "development":
        fitted = fit(data["train"])
        dump("results/fitted_peers.json", fitted)
        csvout("results/source_eligibility.csv", data["audit"])
        csvout("results/pair_eligibility.csv", data["pair_audit"])
        dump(
            "results/coverage.json",
            dict(
                source_rows=len(data["rows"]),
                eligible_rows=len(data["eligible"]),
                train_n=len(data["train"]),
                development_pairs=len(data["pairs"]["development"]),
                held_pair_count_quality_only=len(data["pairs"]["held"]),
                by_year=dict(Counter(r["year"] for r in data["eligible"])),
                exclusion_reasons=dict(
                    Counter(
                        reason
                        for r in data["audit"]
                        for reason in r["reasons"].split(";")
                        if reason
                    )
                ),
            ),
        )
        summary = evaluate(
            "development", data["pairs"]["development"], [*CONFIGS, LAG], fitted
        )
        candidates = summary[:6]
        selected = min(
            enumerate(candidates),
            key=lambda item: (F(item[1]["overall"]["mae_exact"]), item[0]),
        )
        config = CONFIGS[selected[0]]
        dump(
            "results/selection.json",
            dict(
                config=config,
                primary_metric="Equal-building next-year validation MAE",
                mae_exact=selected[1]["overall"]["mae_exact"],
                peer_candidates=6,
                tie_break="Lower fixed candidate index",
                held_outcome_summaries_computed=False,
            ),
        )
        print(
            json.dumps(
                dict(
                    stage="development",
                    selection=read("results/selection.json"),
                    metrics=summary,
                )
            )
        )
    else:
        for name, digest in read("results/frozen_dependencies.json")["sha256"].items():
            assert (
                hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            ), name
        fitted = read("results/fitted_peers.json")
        config = read("results/selection.json")["config"]
        configs = list(
            {c["id"]: c for c in [config, CONFIGS[0], CONFIGS[1], LAG]}.values()
        )
        summary = evaluate("held", data["pairs"]["held"], configs, fitted)
        reviews = review_queues(data, config, fitted)
        print(json.dumps(dict(stage="held", metrics=summary, reviews=reviews)))


if __name__ == "__main__":
    main()
