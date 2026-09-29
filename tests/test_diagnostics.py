import asyncio
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from app import server as s
from app.diagnostics import safe_error
from app.integrations import IntegrationError


def test_error_redacts_credentials_and_contacts(monkeypatch):
    monkeypatch.setenv('SABY_SECRET_KEY','super-secret')
    detail=safe_error(Exception('super-secret Ivan +79991234567 https://api.test/?token=hidden'),{'name':'Ivan','phone':'+79991234567'})
    assert all(value not in json.dumps(detail) for value in ('super-secret','Ivan','79991234567','token=hidden'))


def test_failed_send_is_saved_and_never_retried(monkeypatch):
    with TestClient(s.app) as c:
        c.get('/')
        body={'store_id':'center','name':'Tester','phone':'+79991234567','slot':c.get('/api/slots/center').json()[0],'items':[{'id':'chips','qty':1}],'request_key':str(uuid.uuid4()),'consent':True}
        oid=c.post('/api/orders',json=body).json()['id']
        o=s.get_order(oid);o.update(payment_status='succeeded',status='paid',saby_phase='queued');s.save(o)
        monkeypatch.setattr(s,'DEMO',False)
        monkeypatch.setattr(s.CACHE,'stores',lambda:s.STORES)
        calls=[]
        async def fail(order,store):
            calls.append(order['id']);raise IntegrationError('HTTP 400: pickup disabled for Tester')
        monkeypatch.setattr(s.SABY,'create',fail)
        asyncio.run(s.tick());asyncio.run(s.tick())
        saved=s.get_order(oid)
        assert calls==[oid]
        assert saved['saby_phase']=='uncertain'
        assert 'pickup disabled' in saved['saby_error']['message']
        assert 'Tester' not in saved['saby_error']['message']


def test_notification_failure_preserves_accepted_order(monkeypatch):
    with TestClient(s.app) as c:
        c.get('/')
        body={'store_id':'center','name':'Tester','phone':'+79991234567','slot':c.get('/api/slots/center').json()[0],'items':[{'id':'chips','qty':1}],'request_key':str(uuid.uuid4()),'consent':True}
        oid=c.post('/api/orders',json=body).json()['id']
        o=s.get_order(oid);o.update(payment_status='succeeded',status='paid',saby_phase='queued');s.save(o)
        monkeypatch.setattr(s,'DEMO',False)
        monkeypatch.setattr(s.CACHE,'stores',lambda:s.STORES)
        ext=str(uuid.uuid4())
        async def create(order,store): return {'externalId':ext}
        def fail(*args): raise RuntimeError('notification failed')
        monkeypatch.setattr(s.SABY,'create',create)
        monkeypatch.setattr(s,'queue',fail)
        with pytest.raises(RuntimeError): asyncio.run(s.tick())
        assert s.get_order(oid)['saby_phase']=='sent'
        assert s.get_order(oid)['saby_id']==ext
