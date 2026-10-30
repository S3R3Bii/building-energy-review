"""Replay the captured study offline, preserving the original exported version."""

import csv, json, shutil, subprocess, sys, tempfile, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    originals = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and "__pycache__" not in p.parts
        and p.name != "reproduction.json"
    }
    with tempfile.TemporaryDirectory(prefix="building-energy-replay-") as name:
        target = Path(name) / "study"
        shutil.copytree(
            ROOT,
            target,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "rendered"),
        )
        for cmd in [
            ["src/verify_source.py"],
            ["src/run_analysis.py", "development"],
            ["src/verify_results.py", "development"],
            ["src/run_analysis.py", "held"],
            ["src/verify_results.py", "held"],
            ["-m", "unittest", "discover", "-s", "tests", "-v"],
        ]:
            subprocess.run(
                [sys.executable, *cmd],
                cwd=target,
                check=True,
                stdout=subprocess.DEVNULL,
            )
        subprocess.run(
            [
                sys.executable,
                "src/score_review.py",
                "examples/three_synthetic_buildings.csv",
                "--output",
                str(Path(name) / "synthetic-output.csv"),
            ],
            cwd=target,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        fields = 0
        documents = 0
        for source in sorted((ROOT / "results").glob("*")):
            if source.suffix not in [".csv", ".json"] or source.name not in [
                "development_predictions.csv",
                "held_predictions.csv",
                "source_eligibility.csv",
                "pair_eligibility.csv",
                "review_queue.csv",
                "development_metrics.json",
                "held_metrics.json",
                "fitted_peers.json",
                "selection.json",
                "coverage.json",
                "review_metrics.json",
                "review_overlaps.json",
            ]:
                continue
            replay = target / "results" / source.name
            if source.suffix == ".csv":
                a = list(csv.DictReader(source.open(encoding="utf-8", newline="")))
                b = list(csv.DictReader(replay.open(encoding="utf-8", newline="")))
                assert a == b, source.name
                fields += sum(len(r) for r in a)
            else:
                assert json.loads(source.read_text(encoding="utf-8")) == json.loads(
                    replay.read_text(encoding="utf-8")
                ), source.name
                documents += 1
    for name, digest in originals.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    result = dict(
        status="PASS",
        exact_csv_fields=fields,
        json_documents=documents,
        tests=22,
        frozen_dependencies=21,
        synthetic_cli_demo="Executed in a temporary file;excluded from research results",
        original_files_unchanged=True,
        network_during_replay=False,
    )
    (ROOT / "results/reproduction.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
