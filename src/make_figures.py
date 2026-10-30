"""Original figures: review composition, forecast context and incomplete follow-up."""
import hashlib,json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1]
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'svg.fonttype':'none','svg.hashsalt':'building-energy-review-v1','axes.spines.top':False,'axes.spines.right':False})
read=lambda p:json.loads((ROOT/p).read_text(encoding='utf-8'))
def main():
 out=ROOT/'figures';out.mkdir(exist_ok=True);geometry=[];hashes={}
 def save(fig,name,artists):
  fig.canvas.draw();height=fig.get_figheight()*fig.dpi
  for gid,value,patch in artists:
   points=patch.get_transform().transform(patch.get_path().vertices[:4]);geometry.append(dict(figure=name,gid=gid,value=float(value),points=[[float(x*72/fig.dpi),float((height-y)*72/fig.dpi)] for x,y in points]))
  for ext in ['pdf','svg','png']:
   path=out/(name+'.'+ext);fig.savefig(path,dpi=160,metadata={'Creator':'Building energy review,study28'} if ext=='pdf' else None)
   if ext=='svg':path.write_bytes(('\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8').splitlines())+'\n').encode())
   hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
  plt.close(fig)
 main=[r for r in read('results/review_metrics.json') if r['capacity_percent']==10];types=['Office','Hotel','Retail Store','Non-Refrigerated Warehouse'];colors=['#1b6a50','#d9903b','#35689a','#80507d'];fig,ax=plt.subplots(figsize=(7.2,3.7));fig.subplots_adjust(left=.1,right=.98,top=.82,bottom=.29);artists=[];bottom=[0,0,0]
 for i,t in enumerate(types):
  values=[r['type_counts'].get(t,0) for r in main];bars=ax.bar(range(3),values,bottom=bottom,color=colors[i],width=.6,label=t,hatch=['','//','..','xx'][i],edgecolor='white',linewidth=.5)
  for j,(v,p) in enumerate(zip(values,bars)):gid=f'composition-{i}-{j}';p.set_gid(gid);artists.append((gid,v,p))
  bottom=[a+b for a,b in zip(bottom,values)]
 ax.set_xticks(range(3),['Peer-relative EUI','Annual consumption','Weather-normalized EUI']);ax.set_ylabel('Selected buildings (count)');ax.set_ylim(0,22);ax.set_yticks([0,5,10,15,20]);ax.set_title('Twenty review slots produce different property mixes',loc='left',pad=16);ax.legend(frameon=False,ncol=2,loc='upper center',bbox_to_anchor=(.5,-.22),fontsize=8);save(fig,'review_composition',artists)
 dev=read('results/development_metrics.json');held=read('results/held_metrics.json');fig,axes=plt.subplots(1,2,figsize=(7.5,3.7));fig.subplots_adjust(left=.16,right=.98,bottom=.2,top=.82,wspace=.7);artists=[]
 groups=[(dev,['Pooled','Type only','25% size','50% size','75% size','100% size','Own prior year'],'Development: 145 pairs'),(held,['Type only','Pooled','Own prior year'],'Held: 119 pairs')]
 for j,(group,labels,title) in enumerate(groups):
  ax=axes[j];values=[float(r['overall']['mae']) for r in group];bars=ax.barh(range(len(group)),values,color=['#ac582e' if r['config']=='own_lag' else '#1b6a50' for r in group],height=.65)
  for i,(v,p) in enumerate(zip(values,bars)):gid=f'mae-{j}-{i}';p.set_gid(gid);artists.append((gid,v,p))
  ax.set_yticks(range(len(group)),labels);ax.invert_yaxis();ax.set_xlim(0,32);ax.set_xlabel('MAE (kBtu/ft²/year)');ax.set_title(title,fontsize=10,loc='left',pad=12)
 fig.suptitle('Peer context improves on pooling; own history predicts better',fontsize=11,x=.16,ha='left');save(fig,'prediction_context',artists)
 fig,ax=plt.subplots(figsize=(7.2,3.7));fig.subplots_adjust(left=.1,right=.98,top=.82,bottom=.29);artists=[];bottom=[0,0,0]
 layers=[('above','#1b6a50','', 'Reported ratio > 1.5'),('other','#a9c7b8','..','Reported ratio ≤ 1.5'),('unknown','#d4d4d4','//','Unavailable / outside follow-up rules')]
 for i,(kind,color,hatch,label) in enumerate(layers):
  values=[next(b['above'] for b in r['bands'] if b['ratio']=='3/2') if kind=='above' else r['unavailable_n'] if kind=='unknown' else r['followup_n']-next(b['above'] for b in r['bands'] if b['ratio']=='3/2') for r in main];bars=ax.bar(range(3),values,bottom=bottom,color=color,hatch=hatch,edgecolor='white',linewidth=.5,width=.6,label=label)
  for j,(v,p) in enumerate(zip(values,bars)):gid=f'followup-{i}-{j}';p.set_gid(gid);artists.append((gid,v,p))
  bottom=[a+b for a,b in zip(bottom,values)]
 ax.set_xticks(range(3),['Peer-relative EUI','Annual consumption','Weather-normalized EUI']);ax.set_ylim(0,22);ax.set_yticks([0,5,10,15,20]);ax.set_ylabel('Selected buildings (count)');ax.set_title('Next-year follow-up is incomplete in every review list',loc='left',pad=16);ax.legend(frameon=False,ncol=1,loc='upper center',bbox_to_anchor=(.5,-.22),fontsize=8);save(fig,'followup_coverage',artists)
 (out/'artist_geometry.json').write_bytes((json.dumps(geometry,indent=2)+'\n').encode());(out/'manifest.json').write_bytes((json.dumps(hashes,indent=2,sort_keys=True)+'\n').encode());print('FIGURES_CREATED',len(geometry),'SOURCE_VALUES')
if __name__=='__main__':main()
