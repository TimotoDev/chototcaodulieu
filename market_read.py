"""Bounded multi-source feed. Cursors advance only past consumed records."""
import base64
import hashlib
import json
from datetime import date
from decimal import Decimal
from boto3.dynamodb.conditions import Key
from market_data import SOURCES, listing_day


def parse_filters(params):
    source = params.get('source', 'all')
    if source not in ('all', *SOURCES):
        raise ValueError('Unknown source')
    filters = {'source': source}
    for key in ('brand', 'type', 'region', 'date'):
        value = str(params.get(key, '')).strip()
        if len(value) > 100:
            raise ValueError('Filter too long')
        filters[key] = value.lower()
    if filters['date']:
        date.fromisoformat(filters['date'])
    for key, default in [('min_price', 0), ('max_price', 2_000_000_000)]:
        filters[key] = int(params.get(key) or default)
        if not 0 <= filters[key] <= 2_000_000_000:
            raise ValueError('Invalid price filter')
    if filters['min_price'] > filters['max_price']:
        raise ValueError('Minimum price exceeds maximum')
    return filters


def matches(row, filters):
    for field, key in [('brand', 'brand'), ('type', 'vehicle_type'), ('region', 'region')]:
        if filters[field] and filters[field] not in str(row.get(key) or '').lower():
            return False
    if filters['date'] and filters['date'] != listing_day(row):
        return False
    price = row.get('price_vnd')
    if price is None:
        return filters['min_price'] == 0 and filters['max_price'] == 2_000_000_000
    return filters['min_price'] <= price <= filters['max_price']


def public_item(row):
    price = row.get('price_vnd')
    return {
        'listing_id': row['listing_id'], 'list_id': row['external_id'], 'source': row['source'],
        'subject': row['title'], 'price': price, 'price_str': f'{float(price)/1e6:.1f}M' if price is not None else None,
        'brand': row.get('brand') or '', 'model': row.get('model'), 'type': row.get('vehicle_type') or '',
        'region': row.get('region') or '', 'area': '', 'mileage': row.get('mileage_km'), 'year': row.get('year'),
        'date_added': listing_day(row) if row.get('posted_at') else '',
        'posted_at': row.get('posted_at'), 'observed_at': row['observed_at'], 'last_seen_at': row.get('last_seen_at'),
        'thumbnail': row.get('thumbnail') or '', 'url': row['url'],
    }


def listings_page(table, params, read_budget=600):
    filters = parse_filters(params)
    limit = int(params.get('limit') or 50)
    if not 1 <= limit <= 100:
        raise ValueError('limit must be 1..100')
    fingerprint = hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:24]
    sources = list(SOURCES) if filters['source'] == 'all' else [filters['source']]
    positions = {source: None for source in sources}
    cursor = params.get('cursor')
    if cursor:
        try:
            if len(cursor) > 8192:
                raise ValueError()
            state = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if state.get('v') != 1 or state.get('filter') != fingerprint or set(state['positions']) != set(sources):
                raise ValueError()
            positions = state['positions']
            for source, pos in positions.items():
                if pos is not None and (set(pos) != {'listing_id', 'source', 'source_order'} or pos['source'] != source or any(not isinstance(v, str) or len(v)>400 for v in pos.values()) or not pos['listing_id'].startswith(source + ':')):
                    raise ValueError()
        except Exception as error:
            raise ValueError('Invalid cursor or changed filters') from error
    buffers = {source: [] for source in sources}
    ended = {source: False for source in sources}
    read = 0
    out = []
    while len(out) < limit:
        for source in sources:
            if not buffers[source] and not ended[source] and read < read_budget:
                kwargs = {'IndexName': 'source-order-index', 'KeyConditionExpression': Key('source').eq(source), 'ScanIndexForward': False, 'Limit': min(20, read_budget-read)}
                if positions[source]:
                    kwargs['ExclusiveStartKey'] = positions[source]
                result = table.query(**kwargs)
                buffers[source] = result.get('Items', [])
                read += max(len(buffers[source]), 1)
                ended[source] = not bool(result.get('LastEvaluatedKey'))
        # Do not emit from one source if another unresolved source could contain newer rows.
        if any(not buffers[source] and not ended[source] for source in sources):
            break
        available = [source for source in sources if buffers[source]]
        if not available:
            break
        source = max(available, key=lambda s: buffers[s][0]['source_order'])
        row = buffers[source].pop(0)
        positions[source] = {key: row[key] for key in ('listing_id', 'source', 'source_order')}
        if matches(row, filters):
            out.append(public_item(row))
    more = any(buffers[source] or not ended[source] for source in sources)
    next_cursor = base64.urlsafe_b64encode(json.dumps({'v':1, 'filter':fingerprint, 'positions':positions}, separators=(',', ':')).encode()).decode() if more else None
    return {'items':out, 'total':-1, 'limit':limit, 'next_cursor':next_cursor, 'has_more':more, 'consistency':'eventual', 'read_count':read}


def stats(metadata, source='all'):
    if source not in ('all', *SOURCES):
        raise ValueError('Unknown source')
    snapshot = metadata.get_item(Key={'key': f'snapshot#{source}'}, ConsistentRead=True).get('Item')
    result = {key:value for key,value in (snapshot or {}).items() if key != 'key'}
    if not snapshot:
        result = {'total':0, 'brands':[], 'types':[], 'regions':[], 'daily':[], 'complete':False, 'snapshot_at':None}
    statuses = []
    for name in SOURCES:
        item = metadata.get_item(Key={'key':f'status#{name}'}, ConsistentRead=True).get('Item', {})
        statuses.append({key:item.get(key) for key in ('status','started_at','finished_at','last_success_at','saved','observed','reason','mode')} | {'source':name,'status':item.get('status','never_run')})
    result['sources'] = statuses
    result['date_basis'] = 'source_posted_when_known_else_observed'
    return result
