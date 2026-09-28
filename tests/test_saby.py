import asyncio
import httpx
import pytest
from app.integrations import Saby, IntegrationError
from app import server as s
from fastapi.testclient import TestClient

def test_catalog_pages_and_categories():
    api=Saby(); calls=[]
    async def call(method,path,**kw):
        calls.append(kw['params'])
        if len(calls)==1:
            return {'nomenclatures':[{'isParent':True,'hierarchicalId':1,'name':'Снеки'}, {'id':2,'hierarchicalId':2,'hierarchicalParent':1,'name':'Орехи','unit':'шт','cost':149.90,'balance':'3'}],'outcome':True}
        return {'nomenclatures':[{'id':3,'name':'Весовой','unit':'кг','cost':100}, {'id':4,'name':'Сухарики','unit':'шт','cost':50,'hierarchicalParent':1}], 'outcome':False}
    api.call=call
    rows=asyncio.run(api.catalog({'point_id':10,'price_list_id':20}))
    assert len(rows)==2 and rows[0]['price']==14990 and rows[0]['category']=='Снеки'
    assert calls[1]['position']==2
    assert all(p['priceListId']==20 for p in calls)

def test_selected_points_only(monkeypatch):
    monkeypatch.setenv('SABY_STORE_PRICES','{"10":20}')
    api=Saby()
    async def points(): return [{'id':10,'name':'А','latitude':'58.0','longitude':'56.0'},{'id':11,'name':'Б'}]
    api.points=points
    stores=asyncio.run(api.configured_stores())
    assert len(stores)==1 and stores[0]['price_list_id']==20 and stores[0]['lat']==58.0
    monkeypatch.setenv('SABY_STORE_PRICES','{"12":20}')
    with pytest.raises(IntegrationError): asyncio.run(api.configured_stores())

def test_read_token_refresh(monkeypatch):
    for k in ('SABY_CLIENT_ID','SABY_APP_SECRET','SABY_SECRET_KEY'): monkeypatch.setenv(k,'test')
    tokens=[]; reads=[]
    def handler(request):
        if request.url.path=='/oauth/service/':
            tokens.append(1);return httpx.Response(200,json={'token':str(len(tokens))})
        reads.append(request.headers['X-SBISAccessToken'])
        return httpx.Response(401 if len(reads)==1 else 200,json={})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    asyncio.run(Saby().call('GET','point/list'))
    assert reads==['1','2']

def test_catalog_mode_blocks_writes(monkeypatch):
    monkeypatch.setattr(s,'CATALOG_ONLY',True)
    async def real_catalog(store): return [{'id':'real','name':'Из Saby','price':9900,'saby':{'id':1}}]
    monkeypatch.setattr(s,'catalog',real_catalog)
    with TestClient(s.app) as c:
        assert c.get('/api/config').json()['catalog_only'] is True
        assert c.get('/api/catalog/center').json()==[{'id':'real','name':'Из Saby','price':9900}]
        for url in ('/api/orders','/api/orders/x/demo-pay','/webhooks/yookassa','/webhooks/telegram'):
            assert c.post(url,json={}).status_code==403
        assert c.get('/api/orders').status_code==403

def test_point_pagination():
    api=Saby();calls=[]
    async def call(method,path,**kw):
        calls.append(kw['params']['page'])
        return {'salesPoints':[{'id':len(calls)}], 'outcome':{'hasMore':len(calls)==1}}
    api.call=call
    assert len(asyncio.run(api.points()))==2
    assert calls==[0,1]
