#!/usr/bin/env python3
"""Resumable, rate-limited normalization of existing Chợ Tốt records. Never deletes raw data."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import boto3
from market_data import normalize_chotot, utc_now
from market_store import MarketStore


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile', default='harry')
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--workers', type=int, default=32)
    parser.add_argument('--max-pages', type=int, default=0)
    args=parser.parse_args()
    boto3.setup_default_session(profile_name=args.profile, region_name='ap-southeast-1')
    raw=boto3.resource('dynamodb').Table('chotot-xe-may')
    store=MarketStore()
    path=Path(args.checkpoint)
    state=json.loads(path.read_text()) if path.exists() else {'started_at':utc_now(),'saved':0,'rejected':0,'last_key':None,'complete':False}
    if state['complete']:
        print(json.dumps(state));return
    now=utc_now()
    def save(record):
        try:
            row=normalize_chotot(record,now)
        except ValueError:
            return False
        store.upsert(row)
        return True
    pages=0
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,64))) as pool:
        while True:
            kwargs={'Limit':500}
            if state['last_key']:kwargs['ExclusiveStartKey']={k:int(v) for k,v in state['last_key'].items()}
            result=raw.scan(**kwargs)
            # Persist checkpoint only after every write in this page succeeded.
            outcomes=list(pool.map(save,result.get('Items',[])))
            state['saved']+=sum(outcomes)
            state['rejected']+=len(outcomes)-sum(outcomes)
            state['last_key']=result.get('LastEvaluatedKey')
            state['complete']=not bool(state['last_key'])
            state['updated_at']=utc_now()
            if state['last_key']:state['last_key']={k:int(v) for k,v in state['last_key'].items()}
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state));os.chmod(temp,0o600);temp.replace(path)
            store.metadata.put_item(Item={'key':'backfill#chotot',**state})
            pages+=1
            if pages%10==0 or state['complete']:print(json.dumps(state),flush=True)
            if state['complete'] or (args.max_pages and pages>=args.max_pages):break
    if state['complete']:
        store.save_run({'run_id':'initial-backfill','source':'chotot','mode':'backfill','started_at':state['started_at'],'finished_at':state['updated_at'],'status':'success' if state['saved'] else 'failed','observed':state['saved']+state['rejected'],'saved':state['saved'],'rejected':state['rejected'],'reason':None})


if __name__=='__main__':main()
