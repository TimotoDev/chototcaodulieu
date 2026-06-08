"""
API Lambda for Chotot dashboard
Endpoints:
  GET /api/dates   → list of dates with listing counts
  GET /api/listings?date=YYYY-MM-DD&brand=Honda&type=Tay+ga&min_price=0&max_price=50000000&region=&page=1&limit=50
  GET /api/listings?cursor=<b64>&brand=...  → cursor-based (no date)
  GET /api/stats   → overview stats (total, by brand, by type, by region)
"""
import json, os, boto3, base64
from boto3.dynamodb.conditions import Key
from decimal import Decimal

TABLE = os.environ.get("CHOTOT_DDB_TABLE", "chotot-xe-may")
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")

BRAND_MAP = {1:"Honda",2:"Yamaha",3:"Vespa/Piaggio",4:"SYM",5:"Suzuki",6:"Kawasaki",7:"Ducati",8:"BMW",9:"Harley-Davidson",10:"KTM",11:"Triumph",12:"Royal Enfield",13:"Benelli",14:"Kymco",15:"Peugeot",99:"Khác"}
TYPE_MAP = {1:"Tay ga",2:"Xe số",3:"Xe côn tay",4:"Xe phân khối lớn",5:"Xe điện",6:"Xe 3 bánh"}

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE)

def decimal_default(obj):
    if isinstance(obj, Decimal): return float(obj)
    raise TypeError

def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type":"application/json","Access-Control-Allow-Origin":"*"},
        "body": json.dumps(body, default=decimal_default, ensure_ascii=False)
    }

def get_dates():
    dates = {}
    kwargs = {"IndexName":"date-index","ProjectionExpression":"date_added","Select":"SPECIFIC_ATTRIBUTES"}
    while True:
        result = table.scan(**kwargs)
        for item in result.get("Items",[]):
            d = item.get("date_added","")
            if d: dates[d] = dates.get(d,0) + 1
        if "LastEvaluatedKey" not in result: break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    sorted_dates = [{"date":d,"count":c} for d,c in sorted(dates.items(), reverse=True)]
    return response(200, {"dates": sorted_dates})

def fmt_item(item):
    price = float(item.get("price",0) or 0)
    brand_name = BRAND_MAP.get(int(item.get("motorbikebrand",0) or 0), "")
    type_name = TYPE_MAP.get(int(item.get("motorbiketype",0) or 0), "")
    return {
        "list_id": str(item.get("list_id","")),
        "subject": item.get("subject",""),
        "price": price,
        "price_str": f"{price/1e6:.1f}M",
        "brand": brand_name,
        "type": type_name,
        "region": str(item.get("region_name","") or ""),
        "area": item.get("area_name",""),
        "mileage": item.get("mileage_v2",0),
        "year": item.get("regdate",""),
        "date_added": item.get("date_added",""),
        "thumbnail": item.get("thumbnail_image","") or item.get("webp_image","") or item.get("image",""),
        "url": f"https://xe.chotot.com/mua-ban-xe-may/{item.get('list_id','')}",
        "seller": item.get("account_name",""),
    }

def passes(item, brand, type_, min_price, max_price, region):
    price = float(item.get("price",0) or 0)
    if price < min_price or price > max_price: return False
    brand_name = BRAND_MAP.get(int(item.get("motorbikebrand",0) or 0), "")
    if brand and brand.lower() not in brand_name.lower(): return False
    type_name = TYPE_MAP.get(int(item.get("motorbiketype",0) or 0), "")
    if type_ and type_.lower() not in type_name.lower(): return False
    region_name = str(item.get("region_name","") or "")
    if region and region.lower() not in region_name.lower(): return False
    return True

def get_listings(params):
    date   = params.get("date","")
    brand  = params.get("brand","")
    type_  = params.get("type","")
    min_price = int(params.get("min_price",0) or 0)
    max_price = int(params.get("max_price",999999999) or 999999999)
    region = params.get("region","")
    limit  = min(int(params.get("limit",50) or 50), 100)
    cursor = params.get("cursor","")  # base64 LastEvaluatedKey for no-date mode
    page   = int(params.get("page",1) or 1)

    if date:
        # Load ALL records for this date, filter in memory, paginate by page number
        items = []
        kwargs = {"IndexName":"date-index","KeyConditionExpression":Key("date_added").eq(date)}
        while True:
            result = table.query(**kwargs)
            items.extend(result.get("Items",[]))
            if "LastEvaluatedKey" not in result: break
            kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]

        filtered = [fmt_item(i) for i in items if passes(i, brand, type_, min_price, max_price, region)]
        total = len(filtered)
        start = (page-1)*limit
        return response(200, {
            "total": total, "page": page, "limit": limit,
            "items": filtered[start:start+limit],
            "next_cursor": None, "has_more": start+limit < total
        })

    else:
        # Cursor-based scan — scan until we collect `limit` matching items
        eak = None
        if cursor:
            try: eak = json.loads(base64.b64decode(cursor).decode(), parse_float=Decimal, parse_int=Decimal)
            except: pass

        collected = []
        last_key = None
        scan_kwargs = {"ProjectionExpression":
            "list_id,subject,price,motorbikebrand,motorbiketype,region_name,area_name,"
            "mileage_v2,regdate,date_added,thumbnail_image,webp_image,account_name"
        }
        if eak:
            scan_kwargs["ExclusiveStartKey"] = eak

        while len(collected) < limit:
            result = table.scan(**scan_kwargs)
            for item in result.get("Items",[]):
                if passes(item, brand, type_, min_price, max_price, region):
                    collected.append(fmt_item(item))
            last_key = result.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        next_cursor = None
        if last_key:
            def _serial(obj):
                if isinstance(obj, Decimal): return int(obj) if obj == int(obj) else float(obj)
                raise TypeError
            next_cursor = base64.b64encode(json.dumps(last_key, default=_serial).encode()).decode()

        return response(200, {
            "total": -1, "page": 1, "limit": limit,
            "items": collected[:limit],
            "next_cursor": next_cursor, "has_more": bool(next_cursor)
        })

def get_stats():
    brands = {}; types = {}; regions = {}; daily = {}
    kwargs = {}
    while True:
        result = table.scan(ProjectionExpression="motorbikebrand,motorbiketype,region_name,date_added,price", **kwargs)
        for item in result.get("Items",[]):
            b = BRAND_MAP.get(int(item.get("motorbikebrand",0) or 0),"Khác")
            t = TYPE_MAP.get(int(item.get("motorbiketype",0) or 0),"Khác")
            r = item.get("region_name","Khác") or "Khác"
            d = item.get("date_added","")
            brands[b] = brands.get(b,0)+1
            types[t]  = types.get(t,0)+1
            regions[r] = regions.get(r,0)+1
            if d: daily[d] = daily.get(d,0)+1
        if "LastEvaluatedKey" not in result: break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return response(200, {
        "total": sum(brands.values()),
        "brands":  sorted([{"name":k,"count":v} for k,v in brands.items()],key=lambda x:-x["count"]),
        "types":   sorted([{"name":k,"count":v} for k,v in types.items()],key=lambda x:-x["count"]),
        "regions": sorted([{"name":k,"count":v} for k,v in regions.items()],key=lambda x:-x["count"])[:15],
        "daily":   sorted([{"date":k,"count":v} for k,v in daily.items()],key=lambda x:x["date"])
    })

def handler(event, context):
    path = event.get("rawPath", event.get("path", "/"))
    params = event.get("queryStringParameters") or {}
    if path.endswith("/dates"):    return get_dates()
    elif path.endswith("/listings"): return get_listings(params)
    elif path.endswith("/stats"):  return get_stats()
    return response(404, {"error":"not found"})
