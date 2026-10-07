import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from fastapi import HTTPException
from app.pickup_hours import schedule
from app import server as s

@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for key,value in {'PICKUP_OPEN':'19:00','PICKUP_CLOSE':'02:00','PICKUP_ASSEMBLY_MINUTES':'15','PICKUP_TIMEZONE':'Asia/Yekaterinburg'}.items():
        monkeypatch.setenv(key,value)

def at(value): return schedule(datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Yekaterinburg')))

@pytest.mark.parametrize('clock,accepting',[('18:59:59',False),('19:00:00',True),('23:59:59',True),('00:00:00',True),('01:44:59',True),('01:45:00',False),('02:00:00',False)])
def test_boundaries(clock,accepting):
    assert at('2026-10-07T'+clock)['accepting'] is accepting

def test_midnight_and_rounding():
    assert at('2026-10-07T23:55:30')['slot']=='2026-10-08 00:11:00'
    assert at('2026-10-07T01:44:59')['slot']=='2026-10-07 02:00:00'
    assert at('2026-10-07T02:00:00')['next_open']=='07.10 19:00'

def test_timezone():
    assert schedule(datetime.fromisoformat('2026-10-07T14:00:00+00:00'))['accepting']

def test_daytime(monkeypatch):
    monkeypatch.setenv('PICKUP_OPEN','10:00');monkeypatch.setenv('PICKUP_CLOSE','18:00')
    assert at('2026-10-07T10:00:00')['accepting']
    assert not at('2026-10-07T17:45:00')['accepting']

def test_invalid(monkeypatch):
    monkeypatch.setenv('PICKUP_CLOSE','19:00')
    with pytest.raises(ValueError): at('2026-10-07T20:00:00')

def test_no_calendar_and_no_test_bypass(monkeypatch):
    monkeypatch.setenv('PICKUP_LOCAL_SCHEDULE','true')
    monkeypatch.setenv('TEST_AUTO_PICKUP_TIME','true')
    monkeypatch.setattr(s,'pickup_schedule',lambda:at('2026-10-07T12:00:00'))
    assert s.test_auto_time() is False
    assert asyncio.run(s.slots({}))==[]
    with pytest.raises(HTTPException): s.require_pickup_open()

def test_closed_before_payment_provider(monkeypatch):
    monkeypatch.setenv('PICKUP_LOCAL_SCHEDULE','true')
    monkeypatch.setattr(s,'DEMO',False)
    monkeypatch.setattr(s,'pickup_schedule',lambda:at('2026-10-07T12:00:00'))
    order={'id':'pending-hours','payment_status':'pending','payment_id':None,'store_id':'center','amount':120000}
    monkeypatch.setattr(s,'owned',lambda *a:order)
    async def check(*a): return None
    monkeypatch.setattr(s,'check_pickup',check)
    async def create(*a): pytest.fail('Payment provider called outside pickup hours')
    monkeypatch.setattr(s.YOO,'create',create)
    with pytest.raises(HTTPException): asyncio.run(s.payment('pending-hours','u'))
