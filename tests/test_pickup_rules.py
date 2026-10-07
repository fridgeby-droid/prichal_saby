import asyncio
import os
import time
import uuid
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import server as s
from app.pickup_rules import covers,minimum_amount

RING=[[57.99,56.14],[57.99,56.17],[58.01,56.17],[58.01,56.14],[57.99,56.14]]
POINT={'lat':58,'lon':56.15,'accuracy':10,'captured_at':time.time(),'consent':True}

def test_geometry():
    assert covers([RING],58,56.15,10)
    assert not covers([RING],57,56.15,10)
    assert not covers([RING],58,56.14001,10)
    hole=[[57.999,56.149],[57.999,56.151],[58.001,56.151],[58.001,56.149],[57.999,56.149]]
    assert not covers([RING,hole],58,56.15,10)
    with pytest.raises(ValueError): covers([RING[:-1]],58,56.15,10)

def test_minimum(monkeypatch):
    monkeypatch.delenv('PICKUP_MINIMUM_RUB',raising=False)
    monkeypatch.setenv('PICKUP_MINIMUM_BY_STORE','{"23109":1500}')
    assert minimum_amount('23109')==150000
    assert minimum_amount('other')==120000
    monkeypatch.setenv('PICKUP_MINIMUM_RUB','NaN')
    with pytest.raises(ValueError): minimum_amount('other')

@pytest.fixture
def setup(monkeypatch):
    s.init_db()
    monkeypatch.setenv('PICKUP_GEO_REQUIRED','true')
    monkeypatch.setenv('PICKUP_MINIMUM_RUB','1200')
    monkeypatch.setenv('PICKUP_MINIMUM_BY_STORE','{}')
    monkeypatch.setenv('TEST_AUTO_PICKUP_TIME','true')
    monkeypatch.setattr(s,'current_stores',lambda:[{'id':'23109','point_id':23109,'name':'РЦ','price_list_id':40}])
    async def call(*args,**kwargs): return {'districts':[{'districtId':29425,'coordinates':[RING]}],'outcome':{'hasMore':False}}
    monkeypatch.setattr(s.SABY,'call',call)
    return monkeypatch

def test_profile_and_checkout(setup):
    async def scenario():
        p=await s.set_profile(s.Location(**{**POINT,'captured_at':time.time()}),'geo-user')
        assert p['matches'][0]['store_id']=='23109'
        assert s.profile('another-user') is None
        assert (await s.get_profile('geo-user'))['location']['lat']==58
        async def cat(*args,**kwargs): return [{'id':'item','name':'item','price':60000,'stock':5,'unit':'шт','saby':{}}]
        setup.setattr(s,'catalog',cat)
        def body(q): return s.Checkout(store_id='23109',name='Тест',phone='+79991234567',items=[s.Item(id='item',qty=q)],request_key=uuid.uuid4(),consent=True)
        with pytest.raises(HTTPException): await s.checkout(body(1),'geo-user')
        with pytest.raises(HTTPException): await s.checkout(body(2),'another-user')
        order=await s.checkout(body(2),'geo-user')
        assert order['amount']==120000
        assert s.get_order(order['id'])['pickup_area']['district_id']==29425
        async def empty(*a,**k): return {'districts':{},'outcome':{'hasMore':False}}
        setup.setattr(s.SABY,'call',empty)
        with pytest.raises(HTTPException): await s.payment(order['id'],'geo-user')
        assert s.get_order(order['id'])['payment_id'] is None
        await s.clear_profile('geo-user')
        assert s.profile('geo-user') is None
    asyncio.run(scenario())

def test_bad_locations(setup):
    for changes in ({'lat':float('nan')},{'accuracy':float('inf')},{'accuracy':101}):
        with pytest.raises(ValidationError): s.Location(**{**POINT,**changes})
    async def scenario():
        for changes in ({'consent':False},{'captured_at':0},{'lat':57}):
            with pytest.raises(HTTPException): await s.set_profile(s.Location(**{**POINT,**changes}),'bad-user')
        assert s.profile('bad-user') is None
    asyncio.run(scenario())


def test_unavailable_districts_fail_closed(setup):
    from app.integrations import IntegrationError
    async def unavailable(*a,**k): raise IntegrationError('Unavailable')
    setup.setattr(s.SABY,'call',unavailable)
    async def scenario():
        with pytest.raises(IntegrationError):
            await s.set_profile(s.Location(**{**POINT,'captured_at':time.time()}),'unavailable-user')
        assert s.profile('unavailable-user') is None
    asyncio.run(scenario())
