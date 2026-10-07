"""Daily pickup window in local time; no Saby calendar calls."""
import os
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo


def enabled():
    return os.getenv('PICKUP_LOCAL_SCHEDULE','true').lower()=='true'


def schedule(now=None):
    zone=ZoneInfo(os.getenv('PICKUP_TIMEZONE','Asia/Yekaterinburg'))
    start=time.fromisoformat(os.getenv('PICKUP_OPEN','19:00'))
    end=time.fromisoformat(os.getenv('PICKUP_CLOSE','02:00'))
    minutes=int(os.getenv('PICKUP_ASSEMBLY_MINUTES','15'))
    if start==end or not 1<=minutes<=240 or start.second or end.second:
        raise ValueError('Некорректное расписание самовывоза')
    now=now.astimezone(zone) if now else datetime.now(zone)
    windows=[]
    for offset in (-1,0,1,2):
        day=now.date()+timedelta(days=offset)
        opening=datetime.combine(day,start,zone)
        closing=datetime.combine(day+timedelta(days=end<start),end,zone)
        cutoff=closing-timedelta(minutes=minutes)
        if cutoff<=opening: raise ValueError('Время сборки превышает окно самовывоза')
        windows.append((opening,cutoff,closing))
    active=next((w for w in windows if w[0]<=now<w[1]),None)
    upcoming=next(w for w in windows if w[0]>now)
    ready=now+timedelta(minutes=minutes)
    if ready.second or ready.microsecond: ready=ready.replace(second=0,microsecond=0)+timedelta(minutes=1)
    return {'enabled':True,'accepting':bool(active),'opening':start.strftime('%H:%M'),'closing':end.strftime('%H:%M'),
            'cutoff':windows[1][1].strftime('%H:%M'),'assembly_minutes':minutes,'timezone':str(zone),
            'next_open':upcoming[0].strftime('%d.%m %H:%M'),
            'slot':ready.strftime('%Y-%m-%d %H:%M:%S') if active else None}
