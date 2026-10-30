"""Independent private-original projection check; portable source-hash checks."""
import argparse,gzip,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--private-root',type=Path);a=p.parse_args();manifest=json.loads((ROOT/'data/source_manifest.json').read_text(encoding='utf-8'));rows=json.loads(gzip.decompress((ROOT/'data/records.json.gz').read_bytes()));checks=0
 assert hashlib.sha256((ROOT/'data/records.json.gz').read_bytes()).hexdigest()==manifest['public_projection_sha256'];assert hashlib.sha256((ROOT/'data/field_dictionary.json').read_bytes()).hexdigest()==manifest['dictionary_sha256'];checks+=2
 assert len(rows)==manifest['source_rows'];assert len({r['source_row'] for r in rows})==len(rows);assert all(not any(k in r for k in ['property_id','nyc_building_identification','address_1','property_name']) for r in rows);checks+=3
 mode='PUBLIC_HASH_SCHEMA_ONLY'
 if a.private_root:
  import pandas as pd
  private=a.private_root;original=json.loads((private/'selected.json').read_text(encoding='utf-8'));frame=pd.read_csv(private/'selected.csv',dtype=str,keep_default_na=False)
  for receipt in manifest['requests']:assert hashlib.sha256((private/receipt['name']).read_bytes()).hexdigest()==receipt['sha256'];checks+=1
  fields=list(frame.columns);assert len(fields)==manifest['selected_fields']
  for index,(got,raw) in enumerate(zip(rows,original)):
   values={k:str(raw[k]) if raw.get(k) is not None else '' for k in fields}
   for k in fields:assert frame.iloc[index][k]==values[k];checks+=1
   bid=values.pop('nyc_building_identification').strip();pid=values.pop('property_id').strip();bh=hashlib.sha256(bid.encode()).hexdigest();ph=hashlib.sha256(pid.encode()).hexdigest()
   expected=dict(values,source_row=index,building_key=bh,property_key=ph,bucket=int(bh,16)%5,bin_valid=bool(re.fullmatch(r'[1-5]\d{6}',bid)),property_valid=bool(re.fullmatch(r'[1-9]\d*',pid)),missing_fields=sorted(k for k in fields if raw.get(k) is None))
   assert expected==got;checks+=len(expected)
  assert len(frame)==len(original)==len(rows);before=json.loads((private/'metadata-before.json').read_text(encoding='utf-8'));after=json.loads((private/'metadata-after.json').read_text(encoding='utf-8'));assert before['rowsUpdatedAt']==after['rowsUpdatedAt']==manifest['rows_updated_at'];checks+=2;mode='PRIVATE_ORIGINALS_INDEPENDENT_PANDAS_AND_PROJECTION_PASS'
 result=dict(status='PASS',mode=mode,checks=checks,rows=len(rows),historical_availability='UNKNOWN outside retrospective claim')
 if a.private_root:(ROOT/'data/source_verification.json').write_bytes((json.dumps(result,indent=2)+'\n').encode())
 print(json.dumps(result))
if __name__=='__main__':main()
