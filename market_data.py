"""Public, source-neutral listing contract. Raw seller/session data never belongs here."""
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

SOURCES = ('chotot', 'facebook_marketplace', 'facebook_group')
BRANDS = {1: 'Honda', 2: 'Yamaha', 3: 'Vespa/Piaggio', 4: 'SYM', 5: 'Suzuki', 6: 'Kawasaki', 7: 'Ducati', 8: 'BMW', 9: 'Harley-Davidson', 10: 'KTM', 11: 'Triumph', 12: 'Royal Enfield', 13: 'Benelli', 14: 'Kymco', 15: 'Peugeot', 99: 'Khác'}
TYPES = {1: 'Tay ga', 2: 'Xe số', 3: 'Xe côn tay', 4: 'Xe phân khối lớn', 5: 'Xe điện', 6: 'Xe 3 bánh'}
FIELDS = set('schema_version listing_id source external_id url title price_vnd brand model year year_kind mileage_km region posted_at observed_at parser_version category vehicle_type thumbnail'.split())


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def text(value, limit=200):
    return re.sub(r'[\x00-\x1f\x7f]', '', str(value or '')).strip()[:limit]


def integer(value, minimum=0, maximum=2_000_000_000):
    try:
        n = int(str(value))
        return n if minimum <= n <= maximum else None
    except (ValueError, TypeError):
        return None


def public_title(row):
    return ' '.join(str(row.get(k) or '').strip() for k in ('brand', 'model', 'year')).strip() or 'Motorcycle listing'


def validate_listing(row):
    if set(row) - FIELDS:
        raise ValueError('Unexpected fields in public listing')
    source = row.get('source')
    external_id = row.get('external_id', '')
    if source not in SOURCES or not isinstance(external_id, str) or not re.fullmatch(r'\d+(?::\d+)?', external_id):
        raise ValueError('Invalid source or external ID')
    if row.get('listing_id') != f'{source}:{external_id}':
        raise ValueError('Source-scoped listing ID required')
    url = urlsplit(row.get('url', ''))
    if url.scheme != 'https' or url.username or url.password or url.query or url.fragment or url.port:
        raise ValueError('Invalid listing URL')
    if source == 'chotot':
        valid_url = url.hostname == 'xe.chotot.com' and url.path == f'/mua-ban-xe-may/{external_id}'
    elif source == 'facebook_marketplace':
        valid_url = url.hostname == 'www.facebook.com' and url.path.rstrip('/') == f'/marketplace/item/{external_id}'
    else:
        ids = external_id.split(':')
        valid_url = len(ids) == 2 and url.hostname == 'www.facebook.com' and url.path.rstrip('/') == f'/groups/{ids[0]}/posts/{ids[1]}'
    if not valid_url:
        raise ValueError('URL must match source and ID')
    if source.startswith('facebook') and row.get('title') != public_title(row):
        raise ValueError('Facebook titles must be generated from canonical attributes')
    if row.get('schema_version') != 1 or row.get('category') != 'motorcycles':
        raise ValueError('Unsupported schema/category')
    for key in ('observed_at', 'posted_at'):
        value = row.get(key)
        if value is None and key == 'posted_at':
            continue
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', value):
            raise ValueError('UTC millisecond timestamp required')
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    for key, lo, hi in [('price_vnd', 1, 2_000_000_000), ('year', 1900, datetime.now().year + 1), ('mileage_km', 0, 2_000_000)]:
        value = row.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi):
            raise ValueError(f'Invalid {key}')
    for key in ('title', 'brand', 'model', 'region', 'vehicle_type'):
        value = row.get(key)
        if value is not None and (not isinstance(value, str) or value != text(value)):
            raise ValueError(f'Invalid {key}')
    return row


def normalize_chotot(record, observed_at=None):
    ad = record.get('ad', record)
    external_id = str(ad.get('list_id') or ad.get('ad_id') or '')
    posted = None
    try:
        if ad.get('list_time'):
            posted = datetime.fromtimestamp(int(ad['list_time']) / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except (ValueError, TypeError, OverflowError):
        pass
    thumbnail = text(ad.get('thumbnail_image') or ad.get('webp_image') or ad.get('image'), 2000)
    try:
        u = urlsplit(thumbnail)
        if u.scheme != 'https' or u.username or u.password:
            thumbnail = None
    except ValueError:
        thumbnail = None
    row = {
        'schema_version': 1, 'listing_id': f'chotot:{external_id}', 'source': 'chotot', 'external_id': external_id,
        'url': f'https://xe.chotot.com/mua-ban-xe-may/{external_id}',
        'title': text(ad.get('subject')) or 'Motorcycle listing',
        'price_vnd': integer(ad.get('price'), 1), 'brand': BRANDS.get(integer(ad.get('motorbikebrand'))),
        'model': text(ad.get('motorbikemodel_name')) or None,
        'year': integer(ad.get('regdate'), 1900, datetime.now().year + 1), 'year_kind': 'registration',
        'mileage_km': integer(ad.get('mileage_v2'), 0, 2_000_000),
        'region': text(ad.get('region_name_v3') or ad.get('region_name')) or None,
        'posted_at': posted, 'observed_at': observed_at or utc_now(), 'parser_version': 'chotot-v1',
        'category': 'motorcycles', 'vehicle_type': TYPES.get(integer(ad.get('motorbiketype'))), 'thumbnail': thumbnail or None,
    }
    return validate_listing(row)


def listing_day(row):
    value = row.get('posted_at') or row['observed_at']
    return (datetime.fromisoformat(value.replace('Z', '+00:00')) + timedelta(hours=7)).date().isoformat()
