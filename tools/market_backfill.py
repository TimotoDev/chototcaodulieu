#!/usr/bin/env python3
"""Resumable segmented Chợ Tốt import. Conditional inserts preserve concurrent updates."""
import argparse
import json
import os
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import boto3
from botocore.config import Config
from market_data import normalize_chotot,utc_now
from market_store import MarketStore

FIELDS='list_id subject price motorbikebrand motorbikemodel_name motorbiketype regdate mileage_v2 region_name_v3 region_name list_time thumbnail_image webp_image image'.split()


def record_completion(store,state):
    store.metadata.put_item(Item={'key':'backfill#chotot',**state})
    store.save_run({'run_id':'initial-backfill','source':'chotot','mode':'backfill','started_at':state['started_at'],'finished_at':state['updated_at'],'status':'success' if state['saved'] else 'failed','observed':state['saved']+state['rejected'],'saved':state['saved'],'rejected':state['rejected'],'reason':None})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',default='harry')
    parser.add_argument('--checkpoint',required=True)
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--segments',type=int,default=8)
    parser.add_argument('--max-pages',type=int,default=0,help='Per-segment page cap for a bounded pilot')
    args=parser.parse_args()
    if not 1<=args.segments<=32:raise ValueError('segments must be 1..32')
    boto3.setup_default_session(profile_name=args.profile,region_name='ap-southeast-1')
    raw=boto3.resource('dynamodb',config=Config(max_pool_connections=64,retries={'mode':'standard','max_attempts':8})).Table('chotot-xe-may')
    store=MarketStore();path=Path(args.checkpoint);lock=threading.Lock()
    if path.exists():
        state=json.loads(path.read_text())
        if 'segments' not in state or len(state['segments'])!=args.segments:raise ValueError('Use a fresh checkpoint when changing segmentation')
    else:
        prior=store.metadata.get_item(Key={'key':'backfill#chotot'},ConsistentRead=True).get('Item',{})
        state={'started_at':prior.get('started_at') or utc_now(),'saved':0,'rejected':0,'complete':False,'segments':{str(i):{'saved':0,'rejected':0,'last_key':None,'complete':False} for i in range(args.segments)}}
    if state['complete']:
        record_completion(store,state)
        print(json.dumps(state));return
    observed=state['started_at']
    def persist():
        state['saved']=sum(s['saved'] for s in state['segments'].values())
        state['rejected']=sum(s['rejected'] for s in state['segments'].values())
        state['complete']=all(s['complete'] for s in state['segments'].values())
        state['updated_at']=utc_now()
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state));os.chmod(temp,0o600);temp.replace(path)
        store.metadata.put_item(Item={'key':'backfill#chotot',**state})
    def segment(index):
        current=state['segments'][str(index)]
        if current['complete']:return
        pages=0
        while True:
            kwargs={'Limit':500,'Segment':index,'TotalSegments':args.segments,'ProjectionExpression':','.join(f'#f{i}' for i in range(len(FIELDS))),'ExpressionAttributeNames':{f'#f{i}':field for i,field in enumerate(FIELDS)}}
            if current['last_key']:kwargs['ExclusiveStartKey']={k:int(v) for k,v in current['last_key'].items()}
            result=raw.scan(**kwargs);rows=[];rejected=0
            for record in result.get('Items',[]):
                try:rows.append(normalize_chotot(record,observed))
                except ValueError:rejected+=1
            store.bulk_upsert(rows)
            with lock:
                current['saved']+=len(rows);current['rejected']+=rejected
                current['last_key']={k:int(v) for k,v in result['LastEvaluatedKey'].items()} if result.get('LastEvaluatedKey') else None
                current['complete']=not bool(current['last_key'])
                persist()
                pages+=1
                if pages%10==0 or current['complete']:print(json.dumps({k:state[k] for k in ('saved','rejected','complete','updated_at')}),flush=True)
            if current['complete'] or (args.max_pages and pages>=args.max_pages):break
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,args.segments))) as pool:
        list(pool.map(segment,range(args.segments)))
    if state['complete']:
        record_completion(store,state)


if __name__=='__main__':main()
