import os, tempfile, uuid, time, json, hashlib, hmac
from urllib.parse import urlencode
os.environ['APP_MODE']='demo'
os.environ['DB_PATH']=tempfile.mktemp(suffix='.db')
from fastapi.testclient import TestClient
from app import server as s
import pytest

@pytest.fixture
def client():
    with TestClient(s.app) as c:
        c.get('/')
        yield c

def new_order(c):
    slot=c.get('/api/slots/center').json()[0]
    body={'store_id':'center','name':'Тест','phone':'+79990000000','slot':slot,
          'items':[{'id':'chips','qty':2}],'request_key':str(uuid.uuid4()),'consent':True}
    r=c.post('/api/orders',json=body)
    assert r.status_code==200,r.text
    return r.json(),body

def test_full_cycle_and_idempotency(client):
    o,b=new_order(client)
    assert o['amount']==29800
    assert client.post('/api/orders',json=b).json()['id']==o['id']
    path='/api/orders/'+o['id']
    assert client.post(path+'/demo-next',json={}).status_code==409
    assert client.post(path+'/demo-pay',json={}).json()['status']=='accepted'
    assert client.post(path+'/demo-pay',json={}).json()['status']=='accepted'
    for expected in ['collecting','ready','completed']:
        assert client.post(path+'/demo-next',json={}).json()['status']==expected
    assert client.post(path+'/demo-next',json={}).status_code==409
    assert client.post(path+'/messages',json={'text':'Можно забрать позже?'}).status_code==200
    assert len(client.get(path+'/messages').json())==2

def test_owner_isolation(client):
    o,_=new_order(client)
    with TestClient(s.app) as other:
        other.get('/')
        for suffix in ['', '/messages']:
            assert other.get('/api/orders/'+o['id']+suffix).status_code==404
        assert other.post('/api/orders/'+o['id']+'/demo-pay',json={}).status_code==404

def test_stock_price_and_slot_validation(client):
    _,b=new_order(client)
    b['request_key']=str(uuid.uuid4());b['items'][0]['qty']=25
    assert client.post('/api/orders',json=b).status_code==409
    b['items'][0]['qty']=1;b['slot']='2000-01-01 00:00:00'
    assert client.post('/api/orders',json=b).status_code==409
    b['slot']=client.get('/api/slots/center').json()[0];b['amount']=1
    assert client.post('/api/orders',json=b).json()['amount']==14900

def test_forged_telegram_rejected(client):
    assert client.get('/api/orders',headers={'X-Telegram-Init-Data':'user=123&hash=bad'}).status_code==401
    assert client.post('/webhooks/telegram',json={'update_id':1}).status_code==403

def test_signed_telegram_and_expiration(client,monkeypatch):
    monkeypatch.setenv('BOT_TOKEN','test:secret')
    def signed(age):
        d={'auth_date':str(int(time.time())-age),'user':json.dumps({'id':12345})}
        secret=hmac.new(b'WebAppData',b'test:secret',hashlib.sha256).digest()
        d['hash']=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(d.items())).encode(),hashlib.sha256).hexdigest()
        return urlencode(d)
    assert client.get('/api/orders',headers={'X-Telegram-Init-Data':signed(0)}).status_code==200
    assert client.get('/api/orders',headers={'X-Telegram-Init-Data':signed(90000)}).status_code==401

def test_verified_payment_and_amount(client,monkeypatch):
    import asyncio
    o,_=new_order(client);stored=s.get_order(o['id']);stored['payment_id']='test-id';s.save(stored)
    p={'id':'test-id','metadata':{'order_id':o['id']},'amount':{'value':'1.00','currency':'RUB'},'test':True,'status':'succeeded'}
    with pytest.raises(s.HTTPException): asyncio.run(s.reconcile_payment(stored,p))
    assert s.get_order(o['id'])['payment_status']=='pending'
    p['amount']['value']='298.00';asyncio.run(s.reconcile_payment(stored,p))
    assert s.get_order(o['id'])['status']=='paid'
    asyncio.run(s.reconcile_payment(s.get_order(o['id']),p))
    assert s.get_order(o['id'])['saby_phase']=='queued'

def test_restart_marks_uncertain(client):
    o,_=new_order(client);stored=s.get_order(o['id']);stored.update(saby_phase='sending',payment_status='succeeded');s.save(stored)
    s.init_db()
    assert s.get_order(o['id'])['saby_phase']=='uncertain'
    assert s.get_order(o['id'])['status']=='attention'

def test_chat_shop_scope(client,monkeypatch):
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET','hook-secret')
    monkeypatch.setenv('BOT_TOKEN','')
    o,_=new_order(client)
    monkeypatch.setitem(s.STORES[0],'chat_id',-1001);monkeypatch.setitem(s.STORES[0],'staff_ids',[55])
    with s.connect() as db: db.execute('INSERT INTO replies VALUES(?,?,?) ON CONFLICT(chat,message_id) DO UPDATE SET order_id=excluded.order_id',('-1001',987,o['id']))
    body={'update_id':100,'message':{'chat':{'id':-1001,'type':'supergroup'},'from':{'id':66},'text':'Wrong staff','reply_to_message':{'message_id':987}}}
    h={'X-Telegram-Bot-Api-Secret-Token':'hook-secret'}
    client.post('/webhooks/telegram',json=body,headers=h)
    assert client.get('/api/orders/'+o['id']+'/messages').json()==[]
    body['update_id']=101;body['message']['from']['id']=55
    client.post('/webhooks/telegram',json=body,headers=h)
    client.post('/webhooks/telegram',json=body,headers=h)
    assert len(client.get('/api/orders/'+o['id']+'/messages').json())==1

def test_saby_single_send_and_unknown_result(client,monkeypatch):
    import asyncio
    o,_=new_order(client);stored=s.get_order(o['id']);stored.update(payment_status='succeeded',status='paid',saby_phase='queued');s.save(stored)
    calls=[]
    async def create(order,store):
        calls.append(order['id']);return {'externalId':str(uuid.uuid4())}
    async def state(oid): return {'productState':101}
    monkeypatch.setattr(s,'DEMO',False)
    monkeypatch.setattr(s.CACHE,'stores',lambda:s.STORES)
    monkeypatch.setattr(s.SABY,'create',create);monkeypatch.setattr(s.SABY,'state',state)
    monkeypatch.setenv('SABY_STATE_MAP','{"101":"ready"}')
    asyncio.run(s.tick());asyncio.run(s.tick())
    assert calls==[o['id']]
    assert s.get_order(o['id'])['status']=='ready'
    stored=s.get_order(o['id']);stored.update(saby_phase='queued',status='paid');s.save(stored)
    async def timeout(order,store):
        calls.append(order['id']);raise TimeoutError()
    monkeypatch.setattr(s.SABY,'create',timeout)
    asyncio.run(s.tick());asyncio.run(s.tick())
    assert len(calls)==2
    assert s.get_order(o['id'])['status']=='attention'

def test_webhook_uses_provider_not_incoming_status(client,monkeypatch):
    o,_=new_order(client);stored=s.get_order(o['id']);stored['payment_id']='provider-test';s.save(stored)
    async def get(pid):
        return {'id':pid,'metadata':{'order_id':o['id']},'amount':{'value':'298.00','currency':'RUB'},'test':True,'status':'pending'}
    monkeypatch.setattr(s.YOO,'get',get);monkeypatch.setattr(s,'DEMO',False)
    r=client.post('/webhooks/yookassa',json={'event':'payment.succeeded','object':{'id':'provider-test','status':'succeeded'}})
    assert r.status_code==200
    assert s.get_order(o['id'])['payment_status']=='pending'
