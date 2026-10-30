"""Rank a local, already checked property list using the frozen peer reference."""
import argparse,csv,json,re
from fractions import Fraction as F
from pathlib import Path
from model import ROOT,TYPES,parse,predict,decimal
REQUIRED={'building_key','property_type','area_ft2','weather_eui_kbtu_ft2_year','site_energy_kbtu_year'}
def score(rows,percent=10):
 if type(percent)!=int or not 1<=percent<=100:raise ValueError('Capacity must be an integer from 1 to 100 percent')
 fitted=json.loads((ROOT/'results/fitted_peers.json').read_text(encoding='utf-8'));selected=json.loads((ROOT/'results/selection.json').read_text(encoding='utf-8'))['config'];accepted=[];rejected=[];counts={}
 for row in rows:counts[row.get('building_key','')]=counts.get(row.get('building_key',''),0)+1
 for row in rows:
  reasons=[];key=row.get('building_key','');area=parse(row.get('area_ft2'));eui=parse(row.get('weather_eui_kbtu_ft2_year'));energy=parse(row.get('site_energy_kbtu_year'));kind=row.get('property_type')
  if not re.fullmatch('[A-Za-z0-9_-]{1,128}',key) or counts[key]!=1:reasons.append('missing_unsafe_or_duplicate_key')
  if kind not in TYPES:reasons.append('unsupported_type')
  if area is None or not 25000<=area<=5000000:reasons.append('area_outside_study_scope')
  if eui is None or not 0<eui<=2000:reasons.append('invalid_or_out_of_scope_weather_eui')
  if energy is None or energy<=0:reasons.append('invalid_site_energy')
  if reasons:rejected.append(dict(building_key=key,reasons=';'.join(reasons)));continue
  reference,_=predict(dict(type=kind,area=area,eui=eui),selected,fitted);accepted.append(dict(building_key=key,property_type=kind,area_ft2=decimal(area),weather_eui_kbtu_ft2_year=decimal(eui),site_energy_kbtu_year=decimal(energy),peer_reference=decimal(reference),peer_ratio=decimal(eui/reference),_peer=eui/reference,_energy=energy,_eui=eui))
 for method in ['peer','energy','eui']:
  for rank,row in enumerate(sorted(accepted,key=lambda r:(-r['_'+method],r['building_key'])),1):row[method+'_rank']=rank
 k=max(1,(len(accepted)*percent+99)//100) if accepted else 0
 output=[]
 for row in sorted(accepted,key=lambda r:r['peer_rank']):output.append({k:v for k,v in row.items() if not k.startswith('_')}|{m+'_selected':int(row[m+'_rank']<=k) for m in ['peer','energy','eui']})
 return dict(rows=output,rejected=rejected,capacity_percent=percent,accepted_n=len(output),selected_n=k,notice='Review aid for already quality-checked data. A flag does not establish a fault, waste, compliance or savings; structural peer context is not the strongest forecast comparator.')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('--capacity',type=int,default=10);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
 with a.input.open(encoding='utf-8-sig',newline='') as stream:
  reader=csv.DictReader(stream)
  if not REQUIRED<=set(reader.fieldnames or []):p.error('Missing required columns: '+','.join(sorted(REQUIRED-set(reader.fieldnames or []))))
  rows=list(reader)
 result=score(rows,a.capacity)
 if a.output.exists():p.error('Output already exists; choose a new file to preserve revisions')
 with a.output.open('w',encoding='utf-8',newline='') as stream:
  if result['rows']:writer=csv.DictWriter(stream,fieldnames=list(result['rows'][0]),lineterminator='\n');writer.writeheader();writer.writerows(result['rows'])
 print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
