"""Hourly streaming full aggregate snapshot; no first-page sampling or full-table RAM copy."""
from collections import Counter
from market_data import SOURCES, listing_day, utc_now
from market_store import MarketStore


def accumulator():
    return {'total':0,'priced':0,'unknown_posted':0,**{k:Counter() for k in ('brands','types','regions','daily')}}


def observe(counts,row):
    counts['total']+=1
    counts['priced']+=row.get('price_vnd') is not None
    counts['unknown_posted']+=row.get('posted_at') is None
    for key,field in [('brands','brand'),('types','vehicle_type'),('regions','region')]:
        counts[key][row.get(field) or 'Chưa xác định']+=1
    counts['daily'][listing_day(row)]+=1


def finalize(counts,snapshot_at):
    result={k:counts[k] for k in ('total','priced','unknown_posted')}
    result.update(snapshot_at=snapshot_at,complete=True)
    for key in ('brands','types','regions','daily'):
        count=counts[key]
        result[key]=([{'date':k,'count':v} for k,v in sorted(count.items())][-90:] if key=='daily' else [{'name':k,'count':v} for k,v in count.most_common(20)])
    return result


def build_snapshot(rows,snapshot_at):
    counts=accumulator()
    for row in rows:observe(counts,row)
    return finalize(counts,snapshot_at)


def handler(event,context):
    store=MarketStore();kwargs={};snapshot_at=utc_now()
    groups={source:accumulator() for source in ('all',*SOURCES)}
    while True:
        if context and context.get_remaining_time_in_millis()<20000:
            raise RuntimeError('Snapshot time budget exceeded; previous snapshot retained')
        result=store.listings.scan(**kwargs)
        for row in result.get('Items',[]):
            observe(groups['all'],row)
            observe(groups[row['source']],row)
        if not result.get('LastEvaluatedKey'):break
        kwargs['ExclusiveStartKey']=result['LastEvaluatedKey']
    backfill=store.metadata.get_item(Key={'key':'backfill#chotot'},ConsistentRead=True).get('Item',{})
    for source,counts in groups.items():
        summary=finalize(counts,snapshot_at)
        summary['backfill_complete']=bool(backfill.get('complete'))
        summary['backfill_saved']=backfill.get('saved',0)
        store.metadata.put_item(Item={'key':f'snapshot#{source}',**summary})
    return {'rows':groups['all']['total'],'snapshot_at':snapshot_at,'backfill_complete':bool(backfill.get('complete'))}
