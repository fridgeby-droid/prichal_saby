import asyncio
import hashlib
import json
import time
import pytest
from app import database
from app.catalog_cache import CatalogCache
from app.integrations import IntegrationError

KEY=hashlib.sha256(b'img?params=test').hexdigest()
STORE={'id':'1','point_id':1,'price_list_id':10,'name':'Store','address':'Test','chat_id':''}
PRODUCT={'id':'p','name':'Snack','price':10000,'images':['/api/product-image/'+KEY],'saby':{'id':'p'}}

class FakeSaby:
    def __init__(self):
        self.images={KEY:'img?params=test'}
        self.catalog_calls=0
        self.photo_calls=0
        self.fail=False
        self.photo_fail=False
    async def configured_stores(self): return [STORE]
    async def catalog(self,store):
        self.catalog_calls+=1
        if self.fail: raise IntegrationError('offline')
        return [PRODUCT.copy()]
    async def call(self,*args,**kwargs):
        self.photo_calls+=1
        if self.photo_fail: raise IntegrationError('photo offline')
        return b'normalized image','image/jpeg'

@pytest.fixture
def cache(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('SABY_STORE_PRICES','{"1":10}')
    path=tmp_path/'cache.db'
    database.init(path)
    return CatalogCache(lambda:database.connect(path),FakeSaby(),tmp_path/'photos')

def test_snapshot_reads_and_restart_do_not_call_saby(cache):
    asyncio.run(cache.sync([]))
    for _ in range(5): assert cache.products(STORE)[0]['price']==10000
    restarted=CatalogCache(cache.connect,FakeSaby(),cache.photo_dir)
    assert restarted.products(STORE)[0]['id']=='p'
    assert restarted.local_photo(KEY)[0].read_bytes()==b'normalized image'
    assert restarted.saby.catalog_calls==0
    assert cache.saby.catalog_calls==1
    asyncio.run(cache.sync([]))
    assert cache.saby.photo_calls==1

def test_failed_export_keeps_previous_snapshot(cache):
    asyncio.run(cache.sync([]))
    old=cache.read('catalog:1')
    cache.saby.fail=True
    with pytest.raises(IntegrationError): asyncio.run(cache.sync([]))
    assert cache.read('catalog:1')==old
    assert cache.read('sync_status')[0]['ok'] is False

def test_partial_export_never_publishes(cache):
    asyncio.run(cache.sync([]))
    async def stores(): return [STORE,{**STORE,'id':'2'}]
    async def products(s):
        if s['id']=='2': raise IntegrationError('offline')
        return [{**PRODUCT,'price':20000}]
    cache.saby.configured_stores=stores
    cache.saby.catalog=products
    with pytest.raises(IntegrationError): asyncio.run(cache.sync([]))
    assert cache.products(STORE)[0]['price']==10000
    assert len(cache.stores())==1

def test_stale_prices_block_checkout_only(cache):
    asyncio.run(cache.sync([]))
    with cache.connect() as c:
        c.execute('UPDATE pickup_cache SET updated=? WHERE key=?',(time.time()-901,'catalog:1'))
    assert cache.products(STORE)
    with pytest.raises(IntegrationError): cache.products(STORE,checkout=True)

def test_changed_price_list_never_uses_old_prices(cache):
    asyncio.run(cache.sync([]))
    with pytest.raises(IntegrationError): cache.products({**STORE,'price_list_id':99})

def test_photo_error_does_not_discard_catalogue(cache):
    cache.saby.photo_fail=True
    asyncio.run(cache.sync([]))
    assert cache.products(STORE)
    assert cache.local_photo(KEY) is None
    assert cache.read('sync_status')[0]['photos_failed']==1
    cache.saby.photo_fail=False
    asyncio.run(cache.sync([]))
    assert cache.local_photo(KEY)

def test_path_traversal_and_old_file_cleanup(cache):
    asyncio.run(cache.sync([]))
    assert cache.local_photo('../photos/'+KEY) is None
    old=cache.photo_dir/('a'*64+'.jpg')
    old.write_bytes(b'old')
    import os
    os.utime(old,(0,0))
    cache.prune({KEY})
    assert not old.exists()
    assert cache.local_photo(KEY)

def test_empty_export_replaces_old_goods(cache):
    asyncio.run(cache.sync([]))
    async def empty(store): return []
    cache.saby.catalog=empty
    asyncio.run(cache.sync([]))
    assert cache.products(STORE)==[]

def test_cached_api_survives_saby_outage_and_restart(cache,monkeypatch):
    from app import server as s
    from fastapi.testclient import TestClient
    asyncio.run(cache.sync([]))
    cache.saby.fail=True
    monkeypatch.setattr(s,'CACHE',cache)
    monkeypatch.setattr(s,'MODE','catalog')
    monkeypatch.setattr(s,'DEMO',False)
    monkeypatch.setattr(s,'CATALOG_ONLY',True)
    for key in ('SABY_CLIENT_ID','SABY_APP_SECRET','SABY_SECRET_KEY'):
        monkeypatch.setenv(key,'fake-for-test')
    for _ in range(2):
        with TestClient(s.app) as client:
            assert client.get('/api/config').json()['stores'][0]['id']=='1'
            response=client.get('/api/catalog/1')
            assert response.status_code==200
            assert 'saby' not in response.json()[0]
            response=client.get('/api/product-image/'+KEY)
            assert response.status_code==200
            assert response.content==b'normalized image'
            assert response.headers['cache-control']=='public, max-age=3600'
            assert client.post('/api/orders',json={}).status_code==403

def test_photo_capacity_keeps_existing_files(cache,monkeypatch):
    asyncio.run(cache.sync([]))
    monkeypatch.setenv('PHOTO_CACHE_MAX_MB','1')
    (cache.photo_dir/'other-file').write_bytes(b'x'*1024*1024)
    async def go():
        with pytest.raises(IntegrationError): await cache.save_photo('b'*64,'img?params=another')
    asyncio.run(go())
    assert cache.local_photo(KEY)
    assert cache.local_photo('b'*64) is None
