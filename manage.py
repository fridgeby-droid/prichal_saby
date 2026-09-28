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
    sub.add_parser('saby-points')
    t=sub.add_parser('saby-prices');t.add_argument('point_id',type=int)
    sub.add_parser('saby-check')
    t=sub.add_parser('saby-photos');t.add_argument('--name',default='');t.add_argument('--limit',type=int,default=5)
    t=sub.add_parser('saby-state');t.add_argument('external_id')
    t=sub.add_parser('attach-saby');t.add_argument('order_id');t.add_argument('external_id')
    t=sub.add_parser('retry-saby');t.add_argument('order_id');t.add_argument('--confirmed-absent',action='store_true',required=True)
    args=p.parse_args()
    if args.command=='saby-points':
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
        for store in await s.SABY.configured_stores():
            goods=await s.SABY.catalog(store)
            print(json.dumps({'store':store['name'],'point_id':store['point_id'],'price_list_id':store['price_list_id'],'products':len(goods),'sample':[{'name':p['name'],'price_rub':p['price']/100,'stock':p['stock']} for p in goods[:5]]},ensure_ascii=False,indent=2))
    elif args.command=='telegram-setup':
        assert s.BASE.startswith('https://'),'Нужен PUBLIC_URL с HTTPS'
        secret=os.environ['TELEGRAM_WEBHOOK_SECRET']
        assert len(secret)>=24,'Используйте длинный случайный secret'
        await telegram('setWebhook',{'url':s.BASE+'/webhooks/telegram','secret_token':secret,'allowed_updates':['message']})
        await telegram('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Заказать','web_app':{'url':s.BASE+'/'}}})
        print('Webhook и кнопка меню настроены.')
    elif args.command=='saby-state':
        print(json.dumps(await s.SABY.state(str(uuid.UUID(args.external_id))),ensure_ascii=False,indent=2))
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
