"""External adapters. No external writes in demo mode."""
import os, json, uuid, asyncio, hashlib
from .products import rules, description, image_path
from .photos import download, normalize
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import httpx

TZ = ZoneInfo('Asia/Yekaterinburg')
class IntegrationError(Exception): pass

class Saby:
    def __init__(self): self.token = None; self.auth_lock = asyncio.Lock(); self.images = {}
    async def call(self, method, path, **kwargs):
        binary=kwargs.pop("binary",False)
        async with httpx.AsyncClient(timeout=25) as c:
            for attempt in range(2):
                async with self.auth_lock:
                    if not self.token:
                        keys = ('SABY_CLIENT_ID','SABY_APP_SECRET','SABY_SECRET_KEY')
                        if any(not os.getenv(k) for k in keys):
                            raise IntegrationError('Заполните SABY_CLIENT_ID, SABY_APP_SECRET, SABY_SECRET_KEY')
                        r = await c.post('https://online.sbis.ru/oauth/service/', json=dict(zip(
                            ('app_client_id','app_secret','secret_key'), (os.environ[k] for k in keys))))
                        if r.is_error: raise IntegrationError(f'Saby: авторизация HTTP {r.status_code}')
                        self.token = r.json().get('token') or r.json().get('access_token')
                        if not self.token: raise IntegrationError('Saby: отсутствует токен')
                used_token = self.token
                if binary:
                    try:
                        code,content=await download(c,'https://api.sbis.ru/retail/'+path,used_token)
                    except (ValueError,httpx.HTTPError) as exc:
                        raise IntegrationError(str(exc) if isinstance(exc,ValueError) else 'Фото: ошибка сети Saby') from None
                    if code==401 and attempt==0:
                        if self.token==used_token: self.token=None
                        continue
                    if code!=200: raise IntegrationError(f'Фото: HTTP {code}')
                    try: return normalize(content)
                    except ValueError as exc: raise IntegrationError(str(exc)) from None
                r = await c.request(method, 'https://api.sbis.ru/retail/'+path,
                    headers={'X-SBISAccessToken':used_token}, **kwargs)
                if r.status_code == 401:
                    if self.token == used_token: self.token = None
                    if method == 'GET' and attempt == 0: continue
                if r.is_error:
                    try:
                        error=r.json()
                        detail={k:error[k] for k in ('error','message','details','code') if k in error} if isinstance(error,dict) else {}
                    except ValueError: detail={}
                    raise IntegrationError(f'Saby: {path}, HTTP {r.status_code}. '+json.dumps(detail,ensure_ascii=False)[:3000])
                data = r.json()
                if isinstance(data,dict) and data.get('error'): raise IntegrationError('Ошибка API Saby: '+json.dumps({k:data[k] for k in ('error','message','details','code') if k in data},ensure_ascii=False)[:3000])
                return data

    async def pages(self, path, key, params):
        rows = []
        for page in range(1000):
            result = await self.call('GET', path, params={**params, 'page':page, 'pageSize':100})
            batch = result if isinstance(result,list) else result.get(key)
            # Some price-list responses use a differently named collection.
            if batch is None and isinstance(result,dict):
                candidates = [v for k,v in result.items() if k != 'outcome' and isinstance(v,list)]
                if len(candidates)==1: batch=candidates[0]
            if not isinstance(batch,list): raise IntegrationError('Неизвестный формат ответа Saby: '+path)
            rows.extend(batch)
            outcome = result.get('outcome') if isinstance(result,dict) else None
            more = outcome.get('hasMore') if isinstance(outcome,dict) else outcome
            if not batch or more is False or (more is None and len(batch)<100): return rows
        raise IntegrationError('Превышен лимит страниц Saby')

    async def points(self):
        return await self.pages('point/list','salesPoints',{'product':'retail','withPrices':'true','withSchedule':'true'})

    async def prices(self, point_id):
        return await self.pages('nomenclature/price-list','priceLists',{
            'pointId':point_id,'actualDate':datetime.now(TZ).strftime('%Y-%m-%d')})

    async def configured_stores(self):
        mapping = json.loads(os.getenv('SABY_STORE_PRICES','{}'))
        if not isinstance(mapping,dict) or not mapping:
            raise IntegrationError('Укажите SABY_STORE_PRICES: {"ID_точки": ID_прайса}')
        points = await self.points()
        result=[]
        for point in points:
            pid=str(point['id'])
            if pid not in mapping: continue
            price=mapping[pid]
            if not price: raise IntegrationError('Не указан прайс точки '+pid)
            def coord(key):
                try: return float(point[key])
                except (KeyError,TypeError,ValueError): return None
            result.append({'id':pid,'point_id':point['id'],'price_list_id':price,
                'name':point['name'],'address':point.get('address',''),
                'lat':coord('latitude'),'lon':coord('longitude'),
                'chat_id':json.loads(os.getenv('SABY_STORE_CHATS','{}')).get(pid,'')})
        missing=set(mapping)-{s['id'] for s in result}
        if missing: raise IntegrationError('Saby не вернул точки: '+', '.join(sorted(missing)))
        return result

    async def catalog(self, store):
        rows, position, folders = [], None, {}
        for _ in range(200):
            params={'pointId':store['point_id'],'priceListId':store['price_list_id'],
                    'withBalance':'true','noStopList':'true','pageSize':25,'order':'after'}
            if position is not None: params['position']=position
            result=await self.call('GET','v2/nomenclature/list',params=params)
            batch=result if isinstance(result,list) else result.get('nomenclatures')
            if not isinstance(batch,list): raise IntegrationError('Проверьте формат каталога Saby (nomenclatures)')
            if not batch: break
            for p in batch:
                if p.get('isParent'):
                    folders[str(p.get('hierarchicalId'))]=p.get('name','Каталог')
                    continue
                if p.get('published') is False: continue
                if p.get('cost') is None: continue
                unit=p.get('unit') or ''
                quantity_rules=rules(unit)
                if not quantity_rules: continue
                photos=[]
                for value in ([p['images']] if isinstance(p.get('images'),str) else p.get('images') or []):
                    path=image_path(value)
                    if path:
                        key=hashlib.sha256(path.encode()).hexdigest()
                        self.images[key]=path
                        photos.append('/api/product-image/'+key)
                rows.append({'id':str(p.get('externalId') or p.get('id')), 'name':p['name'],
                    'price':int(Decimal(str(p['cost']))*100), 'stock':None if p.get('balance') is None else max(0,float(Decimal(str(p['balance'])))),
                    'description':description(p.get('description')),'images':photos,'photo_count':len(p.get('images') or []),'photo_format':type(p.get('images')).__name__,**quantity_rules,
                    'category':'Каталог','parent':str(p.get('hierarchicalParent')),'unit':unit, 'icon':'◈',
                    'saby':{k:p[k] for k in ('externalId','id','nomNumber','hierarchicalId') if p.get(k) is not None}})
            outcome=result.get('outcome') if isinstance(result,dict) else None
            if outcome is False or (isinstance(outcome,dict) and outcome.get('hasMore') is False): break
            new_position=batch[-1].get('hierarchicalId')
            if new_position is None or new_position==position: raise IntegrationError('Не удалось продолжить каталог Saby')
            position=new_position
            outcome=result.get('outcome') if isinstance(result,dict) else None
            if outcome is False or (isinstance(outcome,dict) and outcome.get('hasMore') is False): break
        else: raise IntegrationError('Каталог слишком большой для v0.1')
        for row in rows: row['category']=folders.get(row.pop('parent'),'Каталог')
        return rows

    async def slots(self, store):
        data=await self.call('GET','delivery/calendar',params={'pointId':store['point_id']})
        slots=[]
        for day in data.get('dates',[]):
            infos=day.get('IntervalInfo',[])
            if isinstance(infos,dict): infos=[infos]
            for info in infos:
                for n in info.get('Intervals',[]):
                    dt=datetime.fromisoformat(day['date']).replace(tzinfo=TZ)+timedelta(minutes=30*(int(n)+1))
                    if dt>datetime.now(TZ)+timedelta(minutes=10): slots.append(dt.strftime('%Y-%m-%d %H:%M:%S'))
        return sorted(set(slots))[:100]

    async def create(self, order, store):
        body={'product':'delivery','pointId':store['point_id'],
          # externalId is optional. Telegram IDs with a tg: prefix are rejected
          # by Saby; keep the Telegram association in our own order database.
          'customer':{'name':order['name'],'phone':order['phone']},
          'datetime':order['slot'],'comment':f"Mini App #{order['id']} / Оплачено ЮKassa. Закрыть неучитываемым типом оплаты. Повторно оплату не брать.",
          'nomenclatures':[{**i['saby'],'name':i['name'],'count':i['qty'],'cost':i['price']/100,
                           'priceListId':store['price_list_id']} for i in order['items']],
          'delivery':{'isPickup':True,'paymentType':'card'}}
        # paymentType selects the order method only, not a second payment or receipt.
        return await self.call('POST','order/create',json=body)
    async def state(self, external_id):
        return await self.call('GET',f'order/{uuid.UUID(external_id)}/state')

class YooKassa:
    async def call(self, method, path, **kwargs):
        binary=kwargs.pop("binary",False)
        async with httpx.AsyncClient(timeout=25,auth=(os.getenv('YOOKASSA_SHOP_ID',''),os.getenv('YOOKASSA_SECRET_KEY',''))) as c:
            r=await c.request(method,'https://api.yookassa.ru/v3/'+path,**kwargs)
            r.raise_for_status()
            return r.json()
    async def create(self,o,base_url):
        body={'amount':{'value':f"{o['amount']/100:.2f}",'currency':'RUB'},'capture':True,
            'confirmation':{'type':'redirect','return_url':base_url+'/?order='+o['id']},
            'description':'Самовывоз · '+o['id'][:8], 'metadata':{'order_id':o['id']}}
        if os.getenv('RECEIPT_ENABLED','false')=='true':
            body['receipt']={'customer':{'phone':o['phone']},'items':[
                {'description':i['name'][:128],'quantity':str(i['qty']),
                 'amount':{'value':f"{i['price']/100:.2f}",'currency':'RUB'},
                 'vat_code':int(os.environ['RECEIPT_VAT_CODE']), 'payment_mode':'full_prepayment',
                 'payment_subject':'commodity'} for i in o['items']]}
            if os.getenv('RECEIPT_TAX_SYSTEM'): body['receipt']['tax_system_code']=int(os.environ['RECEIPT_TAX_SYSTEM'])
        return await self.call('POST','payments',json=body,headers={'Idempotence-Key':o['id']})
    async def get(self,payment_id):
        if not payment_id or not all(c.isalnum() or c=='-' for c in payment_id): raise IntegrationError('Неверный ID платежа')
        return await self.call('GET','payments/'+payment_id)

async def telegram(method,body):
    token=os.getenv('BOT_TOKEN','')
    if not token: raise IntegrationError('Бот не подключён')
    async with httpx.AsyncClient(timeout=15) as c:
        r=await c.post(f'https://api.telegram.org/bot{token}/{method}',json=body)
        r.raise_for_status()
        data=r.json()
        if not data.get('ok'): raise IntegrationError('Telegram не принял сообщение')
        return data['result']
