import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from score_review import score
def row(key='a'):return dict(building_key=key,property_type='Office',area_ft2='50000',weather_eui_kbtu_ft2_year='90',site_energy_kbtu_year='4500000')
class CliContracts(unittest.TestCase):
 def test_same_capacity_and_ties(self):
  r=score([row('b'),row('a')],10);self.assertEqual(r['selected_n'],1);self.assertEqual(r['rows'][0]['building_key'],'a');self.assertEqual(sum(x['peer_selected'] for x in r['rows']),1)
 def test_rejected_inputs(self):
  r=score([row('a'),row('a'),dict(row('b'),area_ft2='0'),dict(row('c'),weather_eui_kbtu_ft2_year='Not Available')],10);self.assertEqual(r['accepted_n'],0);self.assertEqual(len(r['rejected']),4);self.assertEqual(r['selected_n'],0)
 def test_capacity_validation(self):
  for p in [0,101,True]:
   with self.assertRaises(ValueError):score([row()],p)
if __name__=='__main__':unittest.main()
