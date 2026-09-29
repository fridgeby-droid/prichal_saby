"""Atomic catalogue snapshots and persistent, bounded photo files."""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from .integrations import IntegrationError

log = logging.getLogger(__name__)

class CatalogCache:
    def __init__(self, connect, saby, photo_dir):
        self.connect, self.saby = connect, saby
        self.photo_dir = Path(photo_dir)
        self.lock = asyncio.Lock()

    def read(self, key):
        with self.connect() as c:
            row = c.execute('SELECT data,updated FROM pickup_cache WHERE key=?', (key,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else (None, None)

    def write(self, c, key, data, now):
        c.execute('INSERT INTO pickup_cache(key,data,updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data,updated=excluded.updated',
                  (key, json.dumps(data, ensure_ascii=False), now))

    def stores(self): return self.read('stores')[0]

    def products(self, store, checkout=False):
        snapshot, updated = self.read('catalog:' + store['id'])
        signature = self.signature(store)
        if snapshot is None or snapshot.get('signature') != signature:
            raise IntegrationError('Каталог загружается. Попробуйте через минуту.')
        if checkout and time.time()-updated > int(os.getenv('CATALOG_MAX_AGE_SECONDS','900')):
            raise IntegrationError('Цены давно не обновлялись. Оформление временно недоступно.')
        return snapshot['products']

    @staticmethod
    def signature(store):
        return [str(store['point_id']), str(store['price_list_id'])]

    def local_photo(self, key):
        if not re.fullmatch('[a-f0-9]{64}', key): return None
        for suffix, mime in (('.jpg','image/jpeg'),('.png','image/png')):
            path = self.photo_dir / (key + suffix)
            if path.is_file(): return path, mime
        return None

    async def save_photo(self, key, source):
        path = self.local_photo(key)
        ttl = max(60, int(os.getenv('PHOTO_REFRESH_SECONDS','86400')))
        if path and time.time()-path[0].stat().st_mtime < ttl: return
        content, mime = await self.saby.call('GET', source, binary=True)
        suffix = '.png' if mime == 'image/png' else '.jpg'
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        limit = max(1, int(os.getenv('PHOTO_CACHE_MAX_MB','1024'))) * 1024 * 1024
        used = sum(p.stat().st_size for p in self.photo_dir.iterdir() if p.is_file())
        if used + len(content) > limit:
            raise IntegrationError('Достигнут лимит локального хранилища фото')
        target = self.photo_dir / (key + suffix)
        temp = self.photo_dir / (key + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_bytes(content)
            temp.replace(target)
            other = self.photo_dir / (key + ('.jpg' if suffix == '.png' else '.png'))
            other.unlink(missing_ok=True)
        finally: temp.unlink(missing_ok=True)

    def prune(self, active):
        # Grace period keeps photos referenced by open pages and previous snapshots.
        cutoff = time.time() - 7*86400
        if not self.photo_dir.exists(): return
        for p in self.photo_dir.iterdir():
            if p.suffix in ('.jpg','.png') and re.fullmatch('[a-f0-9]{64}',p.stem):
                if p.stem not in active and p.stat().st_mtime < cutoff: p.unlink()
            elif p.suffix == '.tmp' and p.stat().st_mtime < time.time()-86400:
                p.unlink()

    async def sync(self, stores_file):
        async with self.lock:
            try:
                stores = await self.saby.configured_stores() if os.getenv('SABY_STORE_PRICES') else stores_file
                snapshots, sources = {}, {}
                for store in stores:
                    products = await self.saby.catalog(store)
                    snapshots[store['id']] = {'signature': self.signature(store), 'products': products}
                    for product in products:
                        for url in product.get('images', []):
                            key = url.rsplit('/', 1)[-1]
                            sources[key] = self.saby.images[key]
                # No partial overwrite when one point fails halfway through export.
                now = time.time()
                with self.connect() as c:
                    self.write(c, 'stores', stores, now)
                    for sid, snapshot in snapshots.items(): self.write(c, 'catalog:'+sid, snapshot, now)
                    self.write(c, 'sync_status', {'ok':True,'at':now,'photos_pending':len(sources)}, now)
                self.prune(set(sources))
                semaphore = asyncio.Semaphore(3)
                async def photo_job(key,source):
                    async with semaphore:
                        try:
                            await self.save_photo(key,source)
                            return 0
                        except Exception:
                            log.warning('Photo sync failed: %s',key)
                            return 1
                errors = sum(await asyncio.gather(*(photo_job(k,v) for k,v in sources.items())))
                with self.connect() as c:
                    self.write(c, 'sync_status', {'ok':True,'at':now,'photos_failed':errors,'photos_total':len(sources)}, time.time())
                log.info('Catalogue sync complete: %s points, %s photo errors', len(stores), errors)
                return stores
            except Exception:
                with self.connect() as c:
                    self.write(c, 'sync_status', {'ok':False,'at':time.time(),'error':'Синхронизация Saby не выполнена; сохранена предыдущая версия'}, time.time())
                log.warning('Saby catalogue sync failed; keeping previous snapshot')
                raise
