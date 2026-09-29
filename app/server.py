import asyncio, contextlib, hashlib, hmac, json, logging, os, re, secrets, time, uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager
from decimal import Decimal
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, Response
from .products import line_item
from . import database
from .catalog_cache import CatalogCache
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .integrations import Saby, YooKassa, telegram, TZ, IntegrationError

ROOT=Path(__file__).resolve().parent.parent
MODE=os.getenv('APP_MODE','demo').strip().strip("\"'").lower()
DEMO=MODE=='demo'
CATALOG_ONLY=MODE=='catalog'
BASE=os.getenv('PUBLIC_URL','http://localhost:8000').rstrip('/')
DB=Path(os.getenv('DB_PATH',str(ROOT/'data/app.db')))
STORES=json.loads(Path(os.getenv('STORES_FILE',str(ROOT/'config/stores.json'))).read_text())
SABY=Saby(); YOO=YooKassa(); LOCK=asyncio.Lock()
LABELS={'awaiting_payment':'Ожидает оплаты','paid':'Оплачен · передаём магазину','accepted':'Принят магазином',
        'collecting':'Собираем','ready':'Готов к самовывозу','completed':'Выдан','canceled':'Отменён',
        'attention':'Оплачен · уточняем передачу магазину'}

def connect(): return database.connect(DB)

CACHE=CatalogCache(connect,SABY,os.getenv('PHOTO_DIR',str(DB.parent/'photos')))

def init_db():
    database.init(DB)
    with connect() as c:
        # An interrupted POST must not be blindly retried.
        for row in c.execute('SELECT data FROM orders').fetchall():
            o=json.loads(row[0])
            if o.get('saby_phase')=='sending':
                o.update(saby_phase='uncertain',status='attention')
                c.execute('UPDATE orders SET data=? WHERE id=?',(json.dumps(o,ensure_ascii=False),o['id']))

def all_orders():
    with connect() as c: return sorted([json.loads(r[0]) for r in c.execute('SELECT data FROM orders')],key=lambda o:o['created'],reverse=True)
def get_order(oid):
    with connect() as c: r=c.execute('SELECT data FROM orders WHERE id=?',(oid,)).fetchone()
    if not r: raise HTTPException(404,'Заказ не найден')
    return json.loads(r[0])
def save(o):
    with connect() as c: c.execute('UPDATE orders SET data=? WHERE id=?',(json.dumps(o,ensure_ascii=False),o['id']))
def current_stores():
    if DEMO: return STORES
    stores=CACHE.stores() or []
    mapping=json.loads(os.getenv('SABY_STORE_PRICES','{}'))
    chats=json.loads(os.getenv('SABY_STORE_CHATS','{}'))
    staff=json.loads(os.getenv('SABY_STORE_STAFF','{}'))
    if mapping:
        stores=[{**s,'price_list_id':mapping[s['id']]} for s in stores if s['id'] in mapping]
    return [{**s,'chat_id':chats.get(s['id'],s.get('chat_id','')),'staff_ids':staff.get(s['id'],s.get('staff_ids',[]))} for s in stores]

def store_for(sid):
    for s in current_stores():
        if s['id']==sid: return s
    raise HTTPException(404,'Магазин не найден')
def queue(chat,text,oid):
    if not chat or not os.getenv('BOT_TOKEN'): return
    with connect() as c: c.execute('INSERT INTO outbox(chat,text,order_id) VALUES(?,?,?)',(str(chat),text,oid))
def add_message(oid,sender,text):
    with connect() as c: c.execute('INSERT INTO messages(order_id,sender,text,created) VALUES(?,?,?,?)',(oid,sender,text,time.time()))
def status(o,new):
    if new==o['status']: return
    o['status']=new; save(o)
    queue(o['user_id'],f"Заказ #{o['id'][:8]}: {LABELS[new]}",o['id'])
def view(o):
    keys=('id','store_id','name','phone','address','slot','amount','status','payment_status','created','payment_url')
    d={k:o.get(k) for k in keys}; d['status_label']=LABELS[o['status']]
    d['store']=store_for(o['store_id'])['name']
    d['items']=[{k:i.get(k) for k in ('id','name','qty','price','unit','selection','line_amount','weighted')} for i in o['items']]
    return d

async def user(request:Request):
    if CATALOG_ONLY: return 'catalog-visitor'
    raw=request.headers.get('X-Telegram-Init-Data','')
    if DEMO and not raw:
        session=request.cookies.get('demo_session','')
        if not re.fullmatch(r'[0-9a-f]{48}',session): raise HTTPException(401,'Откройте главную страницу')
        return 'demo:'+session
    try:
        pairs=parse_qsl(raw,keep_blank_values=True)
        if len(dict(pairs))!=len(pairs): raise ValueError()
        data=dict(pairs); expected=data.pop('hash')
        token=os.environ['BOT_TOKEN']
        secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
        check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
        actual=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        age=time.time()-int(data['auth_date'])
        if not hmac.compare_digest(actual,expected) or not -30<=age<=86400: raise ValueError()
        uid=str(json.loads(data['user'])['id'])
        if not uid.isdigit(): raise ValueError()
        return uid
    except (ValueError,KeyError,TypeError): raise HTTPException(401,'Откройте приложение заново через Telegram')

def owned(oid,uid):
    o=get_order(oid)
    if o['user_id']!=uid: raise HTTPException(404,'Заказ не найден')
    return o

def demo_catalog(sid):
    goods=[('chips','Картофельные чипсы',14900,'Снеки','◒'),('nuts','Фисташки, 100 г',23900,'Снеки','◈'),
      ('cheese','Сырные палочки, 100 г',17900,'Закуски','▱'),('crackers','Сухарики с чесноком',8900,'Снеки','▥'),
      ('lemonade','Лимонад, 0,5 л',11900,'Напитки','◉'),('water','Вода, 0,5 л',5900,'Напитки','◌')]
    return [{'id':a,'name':b,'price':c,'category':d,'icon':e,'unit':'шт','stock':20 if sid=='center' else 12,'saby':{'nomNumber':a}} for a,b,c,d,e in goods]
async def catalog(s,checkout=False): return demo_catalog(s['id']) if DEMO else CACHE.products(s,checkout)
def test_auto_time():
    enabled=os.getenv('TEST_AUTO_PICKUP_TIME','false').lower()=='true'
    if enabled and (os.getenv('ALLOW_REAL_PAYMENTS')=='true' or
                    (not DEMO and not os.getenv('YOOKASSA_SECRET_KEY','').startswith('test_'))):
        raise RuntimeError('TEST_AUTO_PICKUP_TIME разрешён только с тестовым ключом ЮKassa и ALLOW_REAL_PAYMENTS=false')
    return enabled

async def slots(s):
    if test_auto_time(): return []
    if not DEMO: return await SABY.slots(s)
    now=datetime.now(TZ); first=now.replace(second=0,microsecond=0)+timedelta(minutes=30-now.minute%30)
    return [(first+timedelta(minutes=30*n)).strftime('%Y-%m-%d %H:%M:%S') for n in range(1,17)]

async def reconcile_payment(o,p):
    if p.get('id')!=o.get('payment_id') or p.get('metadata',{}).get('order_id')!=o['id']: raise HTTPException(400,'Платёж не соответствует заказу')
    if p.get('amount',{}).get('currency')!='RUB' or Decimal(p['amount']['value'])*100!=o['amount']: raise HTTPException(400,'Сумма платежа не соответствует заказу')
    if o.get('test_auto_time') and not p.get('test',False): raise HTTPException(409,'Этот заказ предназначен только для тестовой оплаты')
    if not p.get('test',False) and os.getenv('ALLOW_REAL_PAYMENTS')!='true': raise HTTPException(409,'Реальные платежи отключены')
    if o['payment_status']=='succeeded': return
    if p['status']=='succeeded':
        o['payment_status']='succeeded'; o['status']='paid'; o['saby_phase']='queued';save(o)
    elif p['status']=='canceled':
        o['payment_status']='canceled';o['status']='canceled';save(o)

async def tick():
    if CATALOG_ONLY: return
    async with LOCK:
        for o in all_orders():
            if not DEMO and o['payment_status']=='pending' and o.get('payment_id'):
                try: await reconcile_payment(o,await YOO.get(o['payment_id'])); o=get_order(o['id'])
                except Exception: continue
            if o.get('saby_phase')=='queued':
                if DEMO:
                    o['saby_phase']='sent';o['saby_id']='demo-'+o['id'];status(o,'accepted');save(o)
                    continue
                # Persist intent before the non-idempotent remote POST.
                o['saby_phase']='sending';save(o)
                try:
                    result=await SABY.create(o,store_for(o['store_id']))
                    external=result.get('externalId')
                    uuid.UUID(str(external))
                    o.update(saby_id=external,saby_phase='sent');status(o,'accepted');save(o)
                    queue(store_for(o['store_id'])['chat_id'],f"Оплачен заказ #{o['id'][:8]}. Откройте «Доставку» Saby. Для сообщения покупателю ответьте на это сообщение.",o['id'])
                except Exception:
                    o['saby_phase']='uncertain';status(o,'attention');save(o)
                    queue(os.getenv('ADMIN_CHAT_ID'),f"Проверьте Saby: неопределённый результат передачи #{o['id']}. Автоповтор отключён, чтобы не создать дубль.",o['id'])
            elif not DEMO and o.get('saby_phase')=='sent' and o['status'] not in ('completed','canceled'):
                try:
                    result=await SABY.state(o['saby_id'])
                    mapping=json.loads(os.getenv('SABY_STATE_MAP','{}'))
                    new=mapping.get(str(result.get('productState')))
                    if new in LABELS and new not in ('awaiting_payment','paid'): status(o,new)
                    o['last_saby_state']=result;save(o)
                except Exception: pass
        with connect() as c: jobs=c.execute('SELECT * FROM outbox WHERE sent=0 AND attempts<8 ORDER BY id LIMIT 10').fetchall()
        for job in jobs:
            try:
                result=await telegram('sendMessage',{'chat_id':job['chat'],'text':job['text']})
                with connect() as c:
                    c.execute('UPDATE outbox SET sent=1 WHERE id=?',(job['id'],))
                    c.execute('INSERT INTO replies VALUES(?,?,?) ON CONFLICT(chat,message_id) DO UPDATE SET order_id=excluded.order_id',(job['chat'],result['message_id'],job['order_id']))
            except Exception:
                with connect() as c: c.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?',(job['id'],))
async def worker():
    while True:
        try: await tick()
        except Exception: pass
        await asyncio.sleep(15)

async def catalog_worker():
    while True:
        try: await CACHE.sync(STORES)
        except Exception: pass  # Status is persisted; last complete snapshot remains available.
        await asyncio.sleep(max(60,int(os.getenv('CATALOG_SYNC_SECONDS','300'))))

@asynccontextmanager
async def lifespan(app):
    if MODE not in ('demo','catalog','integration'): raise RuntimeError('APP_MODE: demo, catalog или integration')
    if not DEMO:
        if any(not os.getenv(k) for k in ('SABY_CLIENT_ID','SABY_APP_SECRET','SABY_SECRET_KEY')): raise RuntimeError('Заполните ключи Saby')
        mapping=json.loads(os.getenv('SABY_STORE_PRICES','{}'))
        if not isinstance(mapping,dict) or not mapping or any(not v for v in mapping.values()):
            raise RuntimeError('SABY_STORE_PRICES: нужен непустой JSON вида {"ID_точки": ID_прайса}')
    if not DEMO and not CATALOG_ONLY:
        required=['BOT_TOKEN','TELEGRAM_WEBHOOK_SECRET','YOOKASSA_SHOP_ID','YOOKASSA_SECRET_KEY']
        if any(not os.getenv(k) for k in required): raise RuntimeError('Не заполнены параметры интеграции')
        if not BASE.startswith('https://'): raise RuntimeError('Для интеграции нужен PUBLIC_URL с HTTPS')
        if not os.getenv('YOOKASSA_SECRET_KEY','').startswith('test_') and os.getenv('ALLOW_REAL_PAYMENTS')!='true': raise RuntimeError('Разрешён только тестовый ключ ЮKassa')
        if os.getenv('ALLOW_REAL_PAYMENTS')=='true' and os.getenv('LIVE_ACCEPTANCE_CONFIRMED')!='true': raise RuntimeError('Сначала пройдите приёмку по README')
    for key,default in [('CATALOG_SYNC_SECONDS','300'),('CATALOG_MAX_AGE_SECONDS','900'),('PHOTO_REFRESH_SECONDS','86400'),('PHOTO_CACHE_MAX_MB','1024')]:
        if int(os.getenv(key,default))<=0: raise RuntimeError(key+': нужно положительное число')
    test_auto_time()
    with database.single_worker():
        init_db()
        CACHE.photo_dir.mkdir(parents=True,exist_ok=True)
        tasks=[asyncio.create_task(worker())]
        if not DEMO: tasks.append(asyncio.create_task(catalog_worker()))
        try: yield
        finally:
            for task in tasks: task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError): await task

app=FastAPI(title='Причал · Самовывоз',version='0.4.1',lifespan=lifespan,docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'app/static'),name='static')
@app.middleware('http')
async def limits(request,call_next):
    if CATALOG_ONLY and (request.url.path.startswith('/api/orders') or request.url.path.startswith('/webhooks/')):
        return JSONResponse({'detail':'Режим каталога: заказы и оплата ещё не подключены'},status_code=403)
    if int(request.headers.get('content-length','0') or 0)>100000: return JSONResponse({'detail':'Слишком большой запрос'},status_code=413)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer'
    if 'cache-control' not in response.headers: response.headers['Cache-Control']='no-store'
    return response
@app.exception_handler(IntegrationError)
async def saby_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=503)
@app.exception_handler(Exception)
async def external_error(request,exc): return JSONResponse({'detail':'Сервис временно недоступен. Попробуйте ещё раз; заказ сохранён, если был создан.'},status_code=503)
@app.get('/')
async def index(request:Request):
    r=FileResponse(ROOT/'app/static/index.html')
    if DEMO and not re.fullmatch(r'[0-9a-f]{48}',request.cookies.get('demo_session','')):
        r.set_cookie('demo_session',secrets.token_hex(24),httponly=True,secure=BASE.startswith('https'),samesite='lax',max_age=2592000)
    return r
@app.get('/health')
async def health():
    with connect() as c: c.execute('SELECT 1').fetchone()
    return {'ok':True,'mode':MODE,'database':'postgresql' if database.postgres() else 'sqlite','catalog_ready':bool(current_stores()) if not DEMO else True}

@app.get('/api/config')
async def config(): return {'demo':DEMO,'catalog_only':CATALOG_ONLY,'test_auto_pickup_time':test_auto_time(),'stores':[{k:s.get(k) for k in ('id','name','address','lat','lon')} for s in current_stores()],'bot_username':os.getenv('BOT_USERNAME','')}
@app.get('/api/catalog/{sid}')
async def get_catalog(sid:str,uid=Depends(user)): return [{k:v for k,v in p.items() if k!='saby'} for p in await catalog(store_for(sid))]
@app.get('/api/product-image/{key}')
async def product_image(key:str):
    photo=CACHE.local_photo(key)
    if not photo: raise HTTPException(404,'Фото пока не загружено')
    return FileResponse(photo[0],media_type=photo[1],headers={'Cache-Control':'public, max-age=3600'})

@app.get('/api/slots/{sid}')
async def get_slots(sid:str,uid=Depends(user)): return await slots(store_for(sid))

class Item(BaseModel):
    id:str=Field(max_length=128)
    qty:int=Field(ge=1,le=30000)
class Checkout(BaseModel):
    store_id:str=Field(max_length=80)
    name:str=Field(min_length=2,max_length=80)
    phone:str=Field(pattern=r'^\+7\d{10}$')
    address:str=Field(default='',max_length=250)
    slot:str=Field(default='',max_length=30)
    items:list[Item]=Field(min_length=1,max_length=50)
    request_key:uuid.UUID
    consent:bool
@app.post('/api/orders')
async def checkout(body:Checkout,uid=Depends(user)):
    if not body.consent: raise HTTPException(400,'Подтвердите обработку данных для заказа')
    async with LOCK:
        with connect() as c: old=c.execute('SELECT data FROM orders WHERE user_id=? AND request_key=?',(uid,str(body.request_key))).fetchone()
        if old: return view(json.loads(old[0]))
        if sum(o['user_id']==uid and o['payment_status']=='pending' for o in all_orders())>=5: raise HTTPException(429,'Сначала завершите оплату созданных заказов')
        s=store_for(body.store_id); products={p['id']:p for p in await catalog(s,checkout=True)}
        auto_time=test_auto_time()
        chosen_slot=(datetime.now(TZ)+timedelta(minutes=31)).replace(second=0,microsecond=0).strftime('%Y-%m-%d %H:%M:%S') if auto_time else body.slot
        if not auto_time and chosen_slot not in await slots(s): raise HTTPException(409,'Время больше недоступно. Выберите другое')
        if len(set(i.id for i in body.items))!=len(body.items): raise HTTPException(400,'Повтор товара')
        items=[]
        for item in body.items:
            p=products.get(item.id)
            if not p: raise HTTPException(409,'Товар больше недоступен')
            try: items.append(line_item(p,item.qty))
            except ValueError as exc: raise HTTPException(409,str(exc))
        amount=sum(i['line_amount'] for i in items)
        if amount<=0 or amount>10000000: raise HTTPException(400,'Недопустимая сумма')
        o={'id':str(uuid.uuid4()),'user_id':uid,'store_id':s['id'],'name':body.name.strip(),'phone':body.phone,
           'address':body.address,'slot':chosen_slot,'test_auto_time':auto_time,'items':items,'amount':amount,'created':time.time(),
           'status':'awaiting_payment','payment_status':'pending','payment_id':None,'payment_url':None,
           'saby_phase':'none','consent_at':time.time()}
        with connect() as c: c.execute('INSERT INTO orders VALUES(?,?,?,?)',(o['id'],uid,str(body.request_key),json.dumps(o,ensure_ascii=False)))
        return view(o)
@app.post('/api/orders/{oid}/payment')
async def payment(oid:str,uid=Depends(user)):
    async with LOCK:
        o=owned(oid,uid)
        if o['payment_status']!='pending': return view(o)
        if DEMO: return view(o)
        if o.get('test_auto_time') and (os.getenv('ALLOW_REAL_PAYMENTS')=='true' or not os.getenv('YOOKASSA_SECRET_KEY','').startswith('test_')):
            raise HTTPException(409,'Этот заказ предназначен только для тестового магазина ЮKassa')
        if not o.get('payment_id'):
            if time.time()-o['created']>23*3600:
                raise HTTPException(409,'Срок создания платежа истёк. Оформите новый заказ; при сомнениях обратитесь в поддержку')
            p=await YOO.create(o,BASE)
            o['payment_id']=p['id'];o['payment_url']=p['confirmation']['confirmation_url'];save(o)
        return view(o)
@app.get('/api/orders')
async def orders(uid=Depends(user)): return [view(o) for o in all_orders() if o['user_id']==uid]
@app.get('/api/orders/{oid}')
async def order(oid:str,uid=Depends(user)): return view(owned(oid,uid))
@app.post('/api/orders/{oid}/demo-pay')
async def demo_pay(oid:str,uid=Depends(user)):
    if not DEMO: raise HTTPException(404)
    async with LOCK:
        o=owned(oid,uid)
        if o['payment_status']=='pending': o.update(payment_status='succeeded',status='paid',saby_phase='queued');save(o)
    await tick()
    return view(owned(oid,uid))
@app.post('/api/orders/{oid}/demo-next')
async def demo_next(oid:str,uid=Depends(user)):
    if not DEMO: raise HTTPException(404)
    async with LOCK:
        o=owned(oid,uid); new={'accepted':'collecting','collecting':'ready','ready':'completed'}.get(o['status'])
        if not new: raise HTTPException(409,'Недопустимый переход')
        status(o,new)
    return view(o)
@app.post('/webhooks/yookassa')
async def yoo_hook(request:Request):
    if DEMO: raise HTTPException(404)
    body=await request.json();pid=body.get('object',{}).get('id')
    async with LOCK:
        o=next((o for o in all_orders() if o.get('payment_id')==pid and pid),None)
        if not o: return {'ok':True} # worker also reconciles: webhook may precede saving payment_id
        await reconcile_payment(o,await YOO.get(pid)) # Never trust incoming paid flag.
    return {'ok':True}

class Message(BaseModel): text:str=Field(min_length=1,max_length=2000)
@app.get('/api/orders/{oid}/messages')
async def messages(oid:str,uid=Depends(user)):
    owned(oid,uid)
    with connect() as c: return [dict(r) for r in c.execute('SELECT sender,text,created FROM messages WHERE order_id=? ORDER BY id',(oid,))]
@app.post('/api/orders/{oid}/messages')
async def message(oid:str,body:Message,uid=Depends(user)):
    o=owned(oid,uid);text=body.text.strip()
    if not text: raise HTTPException(400,'Введите сообщение')
    add_message(oid,'customer',text)
    queue(store_for(o['store_id'])['chat_id'],f"Заказ #{oid[:8]} · {o['name']}\n{text}\nОтветьте на это сообщение.",oid)
    if DEMO: add_message(oid,'store','Демо-ответ магазина: сообщение получили. Здесь продавец сможет согласовать замену или время получения.')
    return {'ok':True}

@app.post('/webhooks/telegram')
async def tg_hook(request:Request):
    secret=os.getenv('TELEGRAM_WEBHOOK_SECRET','')
    if not secret or not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token',''),secret): raise HTTPException(403)
    data=await request.json(); update_id=data.get('update_id');m=data.get('message',{})
    if not isinstance(update_id,int): raise HTTPException(400)
    async with LOCK:
        with connect() as c:
            if c.execute('SELECT 1 FROM tg_updates WHERE id=?',(update_id,)).fetchone(): return {'ok':True}
        chat=str(m.get('chat',{}).get('id',''));sender=str(m.get('from',{}).get('id',''));text=m.get('text','')[:2000]
        if text and m.get('chat',{}).get('type')=='private':
            if text.startswith('/start'):
                queue(chat,'Откройте каталог кнопкой меню. Для переписки: /order НОМЕР_ЗАКАЗА (полный номер из Mini App), затем ваше сообщение.','')
            elif text.startswith('/order '):
                oid=text.split(maxsplit=1)[1].strip()
                try:
                    owned(oid,sender)
                    with connect() as c: c.execute('INSERT INTO active_chats VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET order_id=excluded.order_id',(sender,oid))
                    queue(chat,'Выбран заказ #'+oid[:8]+'. Напишите сообщение магазину.',oid)
                except HTTPException: queue(chat,'Заказ не найден. Скопируйте полный номер из Mini App.','')
            else:
                reply=m.get('reply_to_message',{}).get('message_id')
                with connect() as c:
                    row=c.execute('SELECT order_id FROM replies WHERE chat=? AND message_id=?',(chat,reply)).fetchone() if reply else None
                    if not row: row=c.execute('SELECT order_id FROM active_chats WHERE user_id=?',(sender,)).fetchone()
                if row and row[0]:
                    try:
                        o=owned(row[0],sender);add_message(o['id'],'customer',text)
                        queue(store_for(o['store_id'])['chat_id'],f"Заказ #{o['id'][:8]}\n{text}\nОтветьте на сообщение.",o['id'])
                    except HTTPException: pass
                else: queue(chat,'Выберите заказ: /order ПОЛНЫЙ_НОМЕР или напишите из Mini App.','')
        elif text:
            s=next((s for s in current_stores() if str(s.get('chat_id',''))==chat and sender in [str(x) for x in s.get('staff_ids',[])]),None)
            reply=m.get('reply_to_message',{}).get('message_id')
            if s and reply:
                with connect() as c: row=c.execute('SELECT order_id FROM replies WHERE chat=? AND message_id=?',(chat,reply)).fetchone()
                if row and row[0]:
                    o=get_order(row[0])
                    if o['store_id']==s['id']:
                        add_message(o['id'],'store',text);queue(o['user_id'],f"Магазин · заказ #{o['id'][:8]}\n{text}",o['id'])
        with connect() as c: c.execute('INSERT INTO tg_updates VALUES(?)',(update_id,))
    return {'ok':True}
