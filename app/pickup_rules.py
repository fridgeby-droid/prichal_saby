"""Service-area geometry. Saby coordinates are latitude, longitude."""
import math
import os
import json
from decimal import Decimal


def minimum_amount(sid):
    overrides=json.loads(os.getenv('PICKUP_MINIMUM_BY_STORE','{}'))
    if not isinstance(overrides,dict): raise ValueError('PICKUP_MINIMUM_BY_STORE должен быть объектом')
    value=Decimal(str(overrides.get(str(sid),os.getenv('PICKUP_MINIMUM_RUB','1200'))))
    if not value.is_finite() or value < 0 or value*100 != int(value*100):
        raise ValueError('Некорректный минимум заказа')
    return int(value*100)


def ring_result(ring, lat, lon):
    if not isinstance(ring,list) or len(ring)<4: raise ValueError('Некорректная граница района')
    points=[]
    for p in ring:
        if not isinstance(p,list) or len(p)!=2: raise ValueError('Некорректная координата')
        a,b=map(float,p)
        if not math.isfinite(a+b) or not -90<=a<=90 or not -180<=b<=180: raise ValueError('Некорректная координата')
        points.append(((b-lon)*111320*math.cos(math.radians(lat)),(a-lat)*111320))
    if points[0]!=points[-1]: raise ValueError('Незамкнутая граница')
    inside=False; distance=float('inf')
    for (x,y),(u,v) in zip(points,points[1:]):
        dx,dy=u-x,v-y
        t=max(0,min(1,-(x*dx+y*dy)/(dx*dx+dy*dy))) if dx*dx+dy*dy else 0
        distance=min(distance,math.hypot(x+t*dx,y+t*dy))
        if (y>0)!=(v>0) and x+(u-x)*(-y)/(v-y)>0: inside=not inside
    return inside,distance


def covers(coordinates,lat,lon,accuracy):
    if not isinstance(coordinates,list) or not coordinates: return False
    outer,distance=ring_result(coordinates[0],lat,lon)
    if not outer or distance<=accuracy: return False
    for hole in coordinates[1:]:
        inside,distance=ring_result(hole,lat,lon)
        if inside or distance<=accuracy: return False
    return True
