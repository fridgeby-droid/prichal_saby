import uuid
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from app import server as s


def test_auto_checkout_never_requests_calendar(monkeypatch):
    monkeypatch.setenv('TEST_AUTO_PICKUP_TIME','true')
    monkeypatch.setenv('ALLOW_REAL_PAYMENTS','false')
    async def forbidden(store): raise AssertionError('Calendar must not be requested')
    monkeypatch.setattr(s,'slots',forbidden)
    with TestClient(s.app) as c:
        c.get('/')
        assert c.get('/api/config').json()['test_auto_pickup_time'] is True
        body={'store_id':'center','name':'Тест','phone':'+79990000000','items':[{'id':'chips','qty':1}],'request_key':str(uuid.uuid4()),'consent':True}
        r=c.post('/api/orders',json=body)
        assert r.status_code==200,r.text
        chosen=datetime.fromisoformat(r.json()['slot']).replace(tzinfo=s.TZ)
        assert timedelta(minutes=29)<chosen-datetime.now(s.TZ)<timedelta(minutes=32)
        assert s.get_order(r.json()['id'])['test_auto_time'] is True
        assert c.post('/api/orders',json=body).json()['id']==r.json()['id']
        monkeypatch.setattr(s,'DEMO',False)
        monkeypatch.setenv('YOOKASSA_SECRET_KEY','live_not_allowed')
        import asyncio
        with pytest.raises(s.HTTPException) as exc:
            asyncio.run(s.payment(r.json()['id'],s.get_order(r.json()['id'])['user_id']))
        assert exc.value.status_code==409


def test_normal_mode_still_requires_valid_time(monkeypatch):
    monkeypatch.setenv('TEST_AUTO_PICKUP_TIME','false')
    with TestClient(s.app) as c:
        c.get('/')
        body={'store_id':'center','name':'Тест','phone':'+79990000000','items':[{'id':'chips','qty':1}],'request_key':str(uuid.uuid4()),'consent':True}
        assert c.post('/api/orders',json=body).status_code==409


def test_test_flag_rejects_live_configuration(monkeypatch):
    monkeypatch.setenv('TEST_AUTO_PICKUP_TIME','true')
    monkeypatch.setenv('ALLOW_REAL_PAYMENTS','true')
    with pytest.raises(RuntimeError): s.test_auto_time()
    monkeypatch.setenv('ALLOW_REAL_PAYMENTS','false')
    monkeypatch.setattr(s,'DEMO',False)
    monkeypatch.setenv('YOOKASSA_SECRET_KEY','live_key')
    with pytest.raises(RuntimeError): s.test_auto_time()
    monkeypatch.setenv('YOOKASSA_SECRET_KEY','test_example')
    assert s.test_auto_time() is True
