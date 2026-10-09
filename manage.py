"""Operator commands. Run inside the same container/environment as main.py."""
import argparse, asyncio, json, os, uuid
from dotenv import load_dotenv
load_dotenv()
from app import server as s
from app.integrations import telegram

async def main():
    p=argparse.ArgumentParser(description='Причал · настройка и сверка')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('telegram-setup')
    sub.add_parser('attention')
    sub.add_parser('support-check')
    t=sub.add_parser('support-attach');t.add_argument('order_id');t.add_argument('thread_id',type=int)
    t=sub.add_parser('support-reset');t.add_argument('order_id');t.add_argument('--confirmed-absent',action='store_true',required=True)
    sub.add_parser('payments-check')
    t=sub.add_parser('encode-ca');t.add_argument('path')
    t=sub.add_parser('db-export');t.add_argument('path')
    t=sub.add_parser('db-import');t.add_argument('path')
    t=sub.add_parser('order-diagnostics');t.add_argument('order_id')
    sub.add_parser('saby-points')
    sub.add_parser('saby-delivery-points')
    sub.add_parser('saby-calendar')
    t=sub.add_parser('saby-districts');t.add_argument('point_id',type=int)
    sub.add_parser('storage-check')
    sub.add_parser('catalog-sync')
    sub.add_parser('catalog-status')
    sub.add_parser('catalog-products')
    t=sub.add_parser('saby-prices');t.add_argument('point_id',type=int)
    sub.add_parser('saby-check')
    t=sub.add_parser('saby-photos');t.add_argument('--name',default='');t.add_argument('--limit',type=int,default=5)
    t=sub.add_parser('saby-state');t.add_argument('external_id')
    t=sub.add_parser('attach-saby');t.add_argument('order_id');t.add_argument('external_id')
    t=sub.add_parser('retry-saby');t.add_argument('order_id');t.add_argument('--confirmed-absent',action='store_true',required=True)
    args=p.parse_args()
    if args.command=='encode-ca':
        import base64
        from pathlib import Path
        pem=Path(args.path).read_bytes()
        if b'-----BEGIN CERTIFICATE-----' not in pem: raise SystemExit('Файл не является PEM-сертификатом')
        print(base64.b64encode(pem).decode())
    elif args.command in ('db-export','db-import'):
        from app.db_transfer import transfer
        from app import database
        database.init(s.DB)
        print(json.dumps(transfer(s.connect,args.path,args.command.removeprefix('db-')),ensure_ascii=False))
    elif args.command=='payments-check':
        for sid in json.loads(os.getenv('SABY_STORE_PRICES','{}')):
            try:
                shop,key=s.YOO.credentials({'store_id':sid})
                print(json.dumps({'point_id':sid,'shop_id':shop,'configured':True,'test_key':key.startswith('test_')},ensure_ascii=False))
            except Exception: print(json.dumps({'point_id':sid,'configured':False}))
    elif args.command=='support-check':
        from app import support,database
        database.init(s.DB)
        chat=await telegram('getChat',{'chat_id':support.group()})
        me=await telegram('getMe',{})
        member=await telegram('getChatMember',{'chat_id':support.group(),'user_id':me['id']})
        with s.connect() as c:
            unresolved=[dict(r) for r in c.execute("SELECT order_id,chat,state FROM support_topics WHERE thread_id IS NULL")]
            pending=c.execute("SELECT COUNT(*) FROM outbox WHERE sent=0 AND chat=?",(support.recipient(),)).fetchone()[0]
        print(json.dumps({'group_id':chat['id'],'topics_enabled':chat.get('is_forum',False),'manage_topics':member.get('can_manage_topics',False),'unresolved_topics':unresolved,'pending_messages':pending},ensure_ascii=False,indent=2))
    elif args.command in ('support-attach','support-reset'):
        from app import support,database
        database.init(s.DB);s.get_order(args.order_id)
        # Run after stopping the application, like other manual reconciliation commands.
        with database.single_worker():
            with s.connect() as c:
                if args.command=='support-attach':
                    if args.thread_id<=0: raise SystemExit('Неверный ID темы')
                    c.execute("INSERT INTO support_topics(order_id,chat,thread_id,state) VALUES(?,?,?,'open') ON CONFLICT(order_id) DO UPDATE SET chat=excluded.chat,thread_id=excluded.thread_id,state='open'",(args.order_id,support.group(),args.thread_id))
                else:
                    c.execute('DELETE FROM support_topics WHERE order_id=? AND thread_id IS NULL',(args.order_id,))
                c.execute('UPDATE outbox SET attempts=0 WHERE order_id=? AND chat=? AND sent=0',(args.order_id,support.recipient()))
        print('Сверка темы сохранена')
    elif args.command=='catalog-products':
        from app import database
        database.init(s.DB)
        result=[]
        for store in s.current_stores():
            products=await s.catalog(store)
            result.append({'store_id':store['id'],'products':[{'id':x['id'],'name':x['name'],'category_path':x.get('category_path',[x.get('category','Каталог')])} for x in products]})
        print(json.dumps(result,ensure_ascii=False,indent=2))
    elif args.command in ('storage-check','catalog-status','catalog-sync'):
        from app import database
        database.init(s.DB)
        if args.command=='storage-check':
            from app import object_storage
            if object_storage.enabled():
                result=await asyncio.to_thread(object_storage.probe)
                with s.connect() as c: c.execute('SELECT 1').fetchone()
                print(json.dumps({'database':'postgresql' if database.postgres() else 'sqlite',**result},ensure_ascii=False))
                return
            s.CACHE.photo_dir.mkdir(parents=True,exist_ok=True)
            probe=s.CACHE.photo_dir/('probe-'+uuid.uuid4().hex)
            try: probe.write_text('ok')
            finally: probe.unlink(missing_ok=True)
            with s.connect() as c: c.execute('SELECT 1').fetchone()
            print(json.dumps({'database':'postgresql' if database.postgres() else 'sqlite','photos_writable':True,'photo_dir':str(s.CACHE.photo_dir)},ensure_ascii=False))
        elif args.command=='catalog-status':
            print(json.dumps(s.CACHE.read('sync_status')[0] or {'status':'Ещё не синхронизирован'},ensure_ascii=False,indent=2))
        else:
            # A second process must not race the background catalogue worker.
            with database.single_worker():
                await s.CACHE.sync(s.STORES)
            print('Каталог синхронизирован')
    elif args.command=='saby-delivery-points':
        # Diagnostic intentionally bypasses collection parsing: null, empty and
        # unexpected responses must remain visible instead of raising a parser error.
        raw=await s.SABY.call('GET','point/list',params={'product':'delivery','withSchedule':'true','page':0,'pageSize':100})
        def scrub(value):
            if isinstance(value,dict):
                return {k:('[скрыто]' if any(word in k.lower() for word in ('phone','email','token','secret','password','authorization')) else scrub(v)) for k,v in value.items()}
            if isinstance(value,list): return [scrub(v) for v in value]
            if isinstance(value,str):
                for key in ('BOT_TOKEN','SABY_CLIENT_ID','SABY_APP_SECRET','SABY_SECRET_KEY','DATABASE_URL','YOOKASSA_SECRET_KEY'):
                    secret=os.getenv(key,'')
                    if secret: value=value.replace(secret,'[скрыто]')
                if s.SABY.token: value=value.replace(s.SABY.token,'[скрыто]')
            return value
        print(json.dumps({'product':'delivery','page':0,'page_size':100,'configured_point_ids':list(json.loads(os.getenv('SABY_STORE_PRICES','{}'))),'response_type':type(raw).__name__,'response':scrub(raw)},ensure_ascii=False,indent=2))
    elif args.command=='saby-districts':
        print(json.dumps(await s.SABY.call('GET','district/list',params={'pointId':args.point_id,'withCoordinates':'true'}),ensure_ascii=False,indent=2))
    elif args.command=='saby-calendar':
        for pid in json.loads(os.environ['SABY_STORE_PRICES']):
            calendar=await s.SABY.call('GET','delivery/calendar',params={'pointId':int(pid)})
            print(json.dumps({'point_id':pid,'calendar':calendar},ensure_ascii=False,indent=2))
    elif args.command=='saby-points':
        print(json.dumps(await s.SABY.points(),ensure_ascii=False,indent=2))
    elif args.command=='saby-prices':
        print(json.dumps(await s.SABY.prices(args.point_id),ensure_ascii=False,indent=2))
    elif args.command=='saby-photos':
        from app.integrations import IntegrationError
        for store in await s.SABY.configured_stores():
            products=await s.SABY.catalog(store)
            selected=[p for p in products if args.name.lower() in p['name'].lower()][:max(1,min(args.limit,20))]
            for p in selected:
                result={'store':store['name'],'product':p['name'],'images_field_type':p.get('photo_format'),'source_count':p.get('photo_count'),'recognized':len(p.get('images',[]))}
                if p.get('images'):
                    key=p['images'][0].rsplit('/',1)[-1]
                    try:
                        data,mime=await s.SABY.call('GET',s.SABY.images[key],binary=True)
                        result.update(status='OK',format=mime,bytes=len(data))
                    except IntegrationError as exc: result['error']=str(exc)
                else: result['status']='Saby не вернул поддерживаемую ссылку на фото'
                print(json.dumps(result,ensure_ascii=False))
    elif args.command=='saby-check':
        from app.diagnostics import catalog_checks
        failures=0
        async for result in catalog_checks(s.SABY):
            print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
            failures+=not result['ok']
        if failures: raise SystemExit(1)
    elif args.command=='telegram-setup':
        assert s.BASE.startswith('https://'),'Нужен PUBLIC_URL с HTTPS'
        secret=os.environ['TELEGRAM_WEBHOOK_SECRET']
        assert len(secret)>=24,'Используйте длинный случайный secret'
        await telegram('setWebhook',{'url':s.BASE+'/webhooks/telegram','secret_token':secret,'allowed_updates':['message']})
        await telegram('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Заказать','web_app':{'url':s.BASE+'/'}}})
        print('Webhook и кнопка меню настроены.')
    elif args.command=='saby-state':
        print(json.dumps(await s.SABY.state(str(uuid.UUID(args.external_id))),ensure_ascii=False,indent=2))
    elif args.command=='order-diagnostics':
        o=s.get_order(args.order_id)
        fields=('id','store_id','status','payment_status','saby_phase','saby_id','slot','test_auto_time','saby_attempt_at','saby_error')
        result={k:o.get(k) for k in fields}
        if not o.get('saby_error') and o.get('saby_phase')=='uncertain':
            result['note']='Причина старой ошибки не сохранена. Сначала проверьте наличие заказа в Saby; повтор автоматически не выполняется.'
        print(json.dumps(result,ensure_ascii=False,indent=2))
    elif args.command=='attention':
        for o in s.all_orders():
            if o.get('saby_phase') in ('uncertain','sending'): print(o['id'],o['store_id'],o['saby_phase'])
    elif args.command in ('attach-saby','retry-saby'):
        # Stop application first to avoid concurrent operator writes.
        o=s.get_order(args.order_id)
        assert o['payment_status']=='succeeded' and o['saby_phase']=='uncertain','Только неопределённые оплаченные заказы'
        if args.command=='attach-saby':
            external=str(uuid.UUID(args.external_id))
            await s.SABY.state(external)
            o.update(saby_id=external,saby_phase='sent',status='accepted')
        else: o.update(saby_phase='queued',status='paid')
        s.save(o);print('Сверка сохранена. Запустите приложение.')

if __name__=='__main__':
    from app.integrations import IntegrationError
    try: asyncio.run(main())
    except IntegrationError as exc: raise SystemExit(str(exc))
