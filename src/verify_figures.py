import hashlib, json, re, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
read = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))


def main():
    expected = {}
    main = [
        r for r in read("results/review_metrics.json") if r["capacity_percent"] == 10
    ]
    for i, t in enumerate(
        ["Office", "Hotel", "Retail Store", "Non-Refrigerated Warehouse"]
    ):
        for j, r in enumerate(main):
            expected[f"composition-{i}-{j}"] = r["type_counts"].get(t, 0)
    for j, name in enumerate(["development", "held"]):
        for i, r in enumerate(read("results/" + name + "_metrics.json")):
            expected[f"mae-{j}-{i}"] = float(r["overall"]["mae"])
    for j, r in enumerate(main):
        above = next(b["above"] for b in r["bands"] if b["ratio"] == "3/2")
        expected[f"followup-0-{j}"] = above
        expected[f"followup-1-{j}"] = r["followup_n"] - above
        expected[f"followup-2-{j}"] = r["unavailable_n"]
    geometry = read("figures/artist_geometry.json")
    assert len(geometry) == len(expected) == 31
    checks = 0
    trees = {}
    for record in geometry:
        assert record["value"] == expected[record["gid"]]
        checks += 1
        name = record["figure"]
        if name not in trees:
            trees[name] = ET.parse(ROOT / "figures" / (name + ".svg"))
        group = trees[name].find('.//*[@id="' + record["gid"] + '"]')
        assert group is not None
        path = group.find("{http://www.w3.org/2000/svg}path")
        values = [
            float(v)
            for v in re.findall(
                r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path.attrib["d"]
            )
        ]
        for a, b in zip(values[:8], [v for point in record["points"] for v in point]):
            assert abs(a - b) < 1e-5
            checks += 1
    for name, digest in read("figures/manifest.json").items():
        assert (
            hashlib.sha256((ROOT / "figures" / name).read_bytes()).hexdigest() == digest
        )
        checks += 1
    result = dict(
        status="PASS",
        checks=checks,
        source_values=31,
        svg_coordinates=248,
        hashes=9,
        scope="Source values,actual rendered bar geometry and exact hashes;not engineering validation",
    )
    (ROOT / "results/figure_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
