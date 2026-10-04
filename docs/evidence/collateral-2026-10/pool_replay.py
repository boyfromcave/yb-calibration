import csv, numpy as np, collections
D='/Users/boy/projects/yellowback-workspace/yb-calibration/data/local/'
rows=list(csv.DictReader(open(D+'pool-shares.csv')))
keys=[r['payout_key'] for r in rows]; t=np.array([int(r['time']) for r in rows])
print('blocks',len(keys),'days',(t[-1]-t[0])/86400)
cnt=collections.Counter(keys); tot=len(keys)
for k,v in cnt.most_common(8): print(k, round(v/tot,3))
names={r['payout_key']:r['pool'] for r in csv.DictReader(open(D+'pool-names.csv'))}
enf={k for k,v in names.items() if v in('ninjaraider.com','mining-dutch.nl','dapool.io')}
W=2016; EF=1008; ER=1210
x=np.array([k in enf for k in keys],int)
c=np.r_[0,np.cumsum(x)]; cntw=c[W:]-c[:-W]   # signals in the window ending at block i+W-1
print('share window: min %.3f p1 %.3f p5 %.3f p50 %.3f'%tuple(np.r_[cntw.min(),np.percentile(cntw,[1,5,50])]/W))
# ENFORCEMENT state machine: set when count < EF, cleared when count >= ER
st=False; runs=[]; start=None
for i,v in enumerate(cntw):
    if not st and v<EF: st=True; start=i
    elif st and v>=ER: st=False; runs.append(i-start)
if st: runs.append(len(cntw)-start)
print('ENFORCEMENT episodes (blocks):', runs[:20], 'max days', max(runs)/1152 if runs else 0)
# without the top pool (ninjaraider offline): share of the rest
top=[k for k,v in names.items() if v=='ninjaraider.com'][0]
x2=np.array([(k in enf and k!=top) for k in keys],int); c2=np.r_[0,np.cumsum(x2)]; w2=c2[W:]-c2[:-W]
print('without ninjaraider: share p50 %.3f max %.3f'%(np.median(w2)/W, w2.max()/W))
# per-day share of top pool
day=(t-t[0])//86400
for name in ('ninjaraider.com','mining-dutch.nl','dapool.io'):
    k=[a for a,b in names.items() if b==name][0]
    s=np.array([kk==k for kk in keys]); d=np.bincount(day,weights=s)/np.bincount(day)
    print(name, 'daily share min %.2f p10 %.2f p50 %.2f max %.2f'%(d.min(),np.percentile(d,10),np.median(d),d.max()), 'days<5%', int((d<0.05).sum()))
