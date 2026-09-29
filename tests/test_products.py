import asyncio
from decimal import Decimal
import pytest
from app.products import line_item, rules, image_path, description
from app.integrations import Saby, YooKassa

@pytest.mark.parametrize('weight,total',[(150,15000),(200,20000),(250,25000),(300,30000)])
def test_weight_and_price(weight,total):
    p={'unit':'кг','price':100000,'stock':1.25}
    i=line_item(p,weight)
    assert i['qty']==weight/1000 and i['line_amount']==total

@pytest.mark.parametrize('weight',[1,50,100,149,151,175,30100])
def test_invalid_weight(weight):
    with pytest.raises(ValueError): line_item({'unit':'кг','price':99900,'stock':None},weight)

def test_fractional_stock_and_rounding():
    p={'unit':'кг','price':99999,'stock':0.19}
    assert line_item(p,150)['line_amount']==15000
    with pytest.raises(ValueError): line_item(p,200)
    assert line_item({'unit':'г','price':100,'stock':500},150)['qty']==150
    assert line_item({'unit':'шт','price':19900,'stock':2},2)['line_amount']==39800
    with pytest.raises(ValueError): line_item({'unit':'шт','price':19900,'stock':100},31)

def test_image_urls_and_description():
    assert image_path('img?params=abc')=='img?params=abc'
    assert image_path('https://api.sbis.ru/retail/img?params=abc')=='img?params=abc'
    for value in ('https://evil.test/img','//evil.test/img','../private','https://api.sbis.ru@evil.test/retail/img','javascript:alert(1)'):
        assert image_path(value) is None
    assert description('<p>Сыр &amp; специи</p><script>bad()</script>')=='Сыр & специи'

def test_catalog_photo_weight_metadata():
    api=Saby()
    async def call(*args,**kw): return {'nomenclatures':[{'id':1,'name':'Сыр','unit':'кг','cost':999,'balance':'0.250','images':['img?params=abc'],'description':'<p>Вкусный сыр</p>'}], 'outcome':False}
    api.call=call
    p=asyncio.run(api.catalog({'point_id':1,'price_list_id':2}))[0]
    assert p['stock']==0.25 and p['min_qty']==150 and p['step_qty']==50
    assert p['images'][0].startswith('/api/product-image/') and p['description']=='Вкусный сыр'

def test_saby_weight_payload():
    api=Saby();captured={}
    async def call(*args,**kw): captured.update(kw['json']);return {}
    api.call=call
    item=line_item({'id':'1','name':'Сыр','unit':'кг','price':99900,'stock':1,'saby':{'id':1}},150)
    order={'id':'test','name':'Тест','phone':'+79990000000','user_id':'1','slot':'2026-09-29 12:00:00','items':[item]}
    asyncio.run(api.create(order,{'point_id':1,'price_list_id':2}))
    assert captured['nomenclatures'][0]['count']==0.15
    assert captured['nomenclatures'][0]['cost']==999

def test_checkout_weight_server_validation(monkeypatch):
    from app import server as s
    from fastapi.testclient import TestClient
    from uuid import uuid4
    async def catalog(store,checkout=False): return [{'id':'weight','name':'Сыр','unit':'кг','stock':0.19,'price':99900,'saby':{'id':1}}]
    monkeypatch.setattr(s,'catalog',catalog)
    with TestClient(s.app) as c:
        c.get('/')
        slot=c.get('/api/slots/center').json()[0]
        body={'store_id':'center','name':'Тест','phone':'+79990000000','slot':slot,'items':[{'id':'weight','qty':150}],'request_key':str(uuid4()),'consent':True}
        response=c.post('/api/orders',json=body)
        assert response.status_code==200
        assert response.json()['amount']==14985
        assert response.json()['items'][0]['qty']==0.15
        for qty in (100,175,200):
            body['items'][0]['qty']=qty;body['request_key']=str(uuid4())
            assert c.post('/api/orders',json=body).status_code==409
