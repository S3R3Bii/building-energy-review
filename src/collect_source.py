"""Capture only fixed factual fields. Source identities stay outside Git."""

import argparse, csv, gzip, hashlib, io, json, re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "report_year",
    "property_id",
    "nyc_building_identification",
    "year_ending",
    "primary_property_type",
    "parent_property_id",
    "construction_status",
    "number_of_buildings",
    "occupancy",
    "property_gfa_self_reported",
    "property_gfa_calculated_1",
    "weather_normalized_site_eui",
    "site_eui_kbtu_ft",
    "site_energy_use_kbtu",
    "estimated_values_energy",
    "alert_energy_meter_has_less",
    "alert_energy_meter_has_gaps",
    "alert_energy_meter_has",
    "alert_energy_no_meters",
]
TYPES = ["Office", "Hotel", "Retail Store", "Non-Refrigerated Warehouse"]
WHERE = "report_year between 2022 and 2024 AND primary_property_type in ('Office','Hotel','Retail Store','Non-Refrigerated Warehouse')"


def dump(p, x):
    p.parent.mkdir(exist_ok=True, parents=True)
    p.write_bytes(
        (json.dumps(x, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
    )


def hashed(s):
    return hashlib.sha256(s.encode()).hexdigest()


def project(items):
    rows = []
    for i, x in enumerate(items):
        raw = {k: str(x[k]) if x.get(k) is not None else "" for k in FIELDS}
        bin_id = raw.pop("nyc_building_identification").strip()
        property_id = raw.pop("property_id").strip()
        rows.append(
            dict(
                source_row=i,
                building_key=hashed(bin_id),
                property_key=hashed(property_id),
                bin_valid=bool(re.fullmatch("[1-5][0-9]{6}", bin_id)),
                property_valid=bool(re.fullmatch("[1-9][0-9]*", property_id)),
                bucket=int(hashed(bin_id), 16) % 5,
                missing_fields=sorted(k for k in FIELDS if x.get(k) is None),
                **raw
            )
        )
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--private-root", required=True, type=Path)
    p.add_argument("--project-only", action="store_true")
    a = p.parse_args()
    private = a.private_root.resolve()
    assert not private.is_relative_to(ROOT)
    private.mkdir(parents=True, exist_ok=True)
    receipts = []

    def get(name, url, params=None):
        target = private / name
        if a.project_only:
            raw = target.read_bytes()
            request_url = requests.Request("GET", url, params=params).prepare().url
            receipts.append(
                dict(
                    name=name,
                    url=request_url,
                    time_basis="Captured-file last-write UTC; original per-response receipt was not retained before the initial parser failure",
                    file_written_utc=datetime.fromtimestamp(
                        target.stat().st_mtime, timezone.utc
                    ).isoformat(),
                    http_status="Not retained",
                    sha256=hashlib.sha256(raw).hexdigest(),
                    bytes=len(raw),
                )
            )

            class Captured:
                content = raw

                def json(self):
                    return json.loads(raw)

            return Captured()
        assert not target.exists(), "Do not replace a captured source"
        now = datetime.now(timezone.utc).isoformat()
        r = requests.get(url, params=params, timeout=45)
        r.raise_for_status()
        target.write_bytes(r.content)
        receipts.append(
            dict(
                name=name,
                url=r.url,
                status=r.status_code,
                utc=now,
                sha256=hashlib.sha256(r.content).hexdigest(),
                bytes=len(r.content),
            )
        )
        dump(private / "capture-receipts.json", receipts)
        return r

    before = get(
        "metadata-before.json", "https://data.cityofnewyork.us/api/views/5zyy-y8am.json"
    ).json()
    count = get(
        "count.json",
        "https://data.cityofnewyork.us/resource/5zyy-y8am.json",
        {"$select": "count(*) AS n", "$where": WHERE},
    ).json()
    n = int(count[0]["n"])
    assert 100 <= n < 50000
    params = {
        "$select": ",".join(FIELDS),
        "$where": WHERE,
        "$order": "report_year,property_id,nyc_building_identification",
        "$limit": 50000,
    }
    csv_response = get(
        "selected.csv", "https://data.cityofnewyork.us/resource/5zyy-y8am.csv", params
    )
    json_response = get(
        "selected.json", "https://data.cityofnewyork.us/resource/5zyy-y8am.json", params
    )
    after = get(
        "metadata-after.json", "https://data.cityofnewyork.us/api/views/5zyy-y8am.json"
    ).json()
    assert (
        before["rowsUpdatedAt"] == after["rowsUpdatedAt"]
    ), "Source changed during capture"
    left = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    right = json_response.json()
    assert len(left) == len(right) == n
    assert list(left[0]) == FIELDS, (list(left[0]), FIELDS)
    checks = 0
    csv_rows = Counter(tuple(row[k] for k in FIELDS) for row in left)
    json_rows = Counter(
        tuple(str(row[k]) if row.get(k) is not None else "" for k in FIELDS)
        for row in right
    )
    assert csv_rows == json_rows, "Record values differ across formats"
    checks = n * len(FIELDS)
    projected = project(right)
    raw = (
        json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data/records.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    schema = [
        {k: c.get(k) for k in ["fieldName", "name", "dataTypeName", "description"]}
        for c in before["columns"]
        if c.get("fieldName") in FIELDS
    ]
    dump(ROOT / "data/field_dictionary.json", schema)
    manifest = dict(
        source="NYC DOB LL84,5zyy-y8am",
        captured_utc=receipts[0].get("utc", receipts[0].get("file_written_utc")),
        requests=receipts,
        source_rows=n,
        selected_fields=len(FIELDS),
        cross_format_cells=checks,
        rows_updated_at=before["rowsUpdatedAt"],
        same_source_update_before_after=True,
        public_projection_sha256=hashlib.sha256(
            (ROOT / "data/records.json.gz").read_bytes()
        ).hexdigest(),
        dictionary_sha256=hashlib.sha256(
            (ROOT / "data/field_dictionary.json").read_bytes()
        ).hexdigest(),
        identity="Full SHA256 BIN/property keys;original identity crosswalks excluded",
        license_id=before.get("licenseId"),
        rights="Minimal factual public projection with DOB attribution;no blanket content license claim",
        vintage="Current snapshot;original publication vintages unavailable",
    )
    dump(ROOT / "data/source_manifest.json", manifest)
    print(
        json.dumps(
            dict(
                status="SOURCE_CAPTURE_AND_CROSS_FORMAT_PASS",
                rows=n,
                cells=checks,
                projection_bytes=len(raw),
            )
        )
    )


if __name__ == "__main__":
    main()
