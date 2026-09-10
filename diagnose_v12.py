"""Read-only reproduction of the V1.2 default selector; never imports .env or runs broker."""
from collections import Counter
from datetime import datetime,timezone,timedelta
import json
from polymarket import PublicClient

def num(x,default=0):
    try:return float(x) if x is not None else default
    except (ValueError,TypeError):return default

def liq(m):
    for source in (getattr(m,'metrics',None),m):
        for key in ('liquidity_num','liquidity'):
            value=num(getattr(source,key,None),-1)
            if value>=0:return value
    return 0

def main():
    now=datetime.now(timezone.utc)
    with PublicClient() as client:
        page=client.list_markets(closed=False,end_date_min=now+timedelta(hours=6),end_date_max=now+timedelta(days=14),order='endDate',ascending=True,page_size=200).first_page()
        reasons=Counter(); eligible=[]
        for m in page.items:
            volume=num(getattr(getattr(m,'metrics',None),'volume_24hr',None))
            if liq(m)<10000:reasons['liquidity']+=1
            elif volume<1000:reasons['volume24h']+=1
            else:eligible.append(m)
        eligible.sort(key=lambda m:abs(num(getattr(m.prices,'one_hour_price_change',None)))*100000+num(getattr(m.metrics,'volume_24hr',None)),reverse=True)
        sample=[]
        for m in eligible[:25]:
            token=m.outcomes.yes.token_id
            if token is None:reasons['missing_yes_token']+=1;continue
            try:
                mid=num(client.get_midpoint(token_id=token),-1)
                spread=num(client.get_spread(token_id=token),-1)
                if not .1<=mid<=.85:reasons['entry_price_range']+=1
                elif not 0<=spread<=.02:reasons['entry_spread']+=1
                else:reasons['can_collect_momentum']+=1
                sample.append(dict(market=str(m.id),question=m.question,mid=mid,spread=spread,change1h=num(getattr(m.prices,'one_hour_price_change',None))))
            except Exception as e:reasons[type(e).__name__]+=1
    result=dict(timestamp=now.isoformat(),configuration='V1.2 source defaults; .env not loaded',fetched=len(page.items),eligible_before_top25=len(eligible),reasons=dict(reasons),samples=sample,
      diagnosis='First page sorted by endDate; short local momentum is still required (8 observations, 0.004 absolute rise); 1h only ranks and 1d not used; no evidence that zero positions means healthy selection.')
    print(json.dumps(result,indent=2))
    open('v12_diagnostic.json','w').write(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
