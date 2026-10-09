"""Bounded operator diagnostics without tokens or customer contact details."""
import os
import re


def safe_error(exc, order=None):
    text=str(exc)
    for key in [k for k in os.environ if 'SECRET' in k or 'PASSWORD' in k or 'ACCESS_KEY' in k] + list(('BOT_TOKEN','DATABASE_URL','SABY_CLIENT_ID','SABY_APP_SECRET','SABY_SECRET_KEY','YOOKASSA_SECRET_KEY','TELEGRAM_WEBHOOK_SECRET')):
        value=os.getenv(key,'')
        if value: text=text.replace(value,'[скрыто]')
    for key in ('name','phone','address','user_id'):
        value=str((order or {}).get(key) or '')
        if value: text=text.replace(value,'[скрыто]')
    text=re.sub(r'https?://[^\s<>"\']+', '[адрес скрыт]',text)
    text=re.sub(r'\+?\d[\d ()-]{9,}\d','[номер скрыт]',text)
    return {'type':type(exc).__name__,'message':text[:1200]}


async def catalog_checks(saby):
    """Read each configured point independently; never change catalogue snapshots."""
    stores=await saby.configured_stores()
    for store in stores:
        result={'store':store['name'],'point_id':store['point_id'],'price_list_id':store['price_list_id']}
        try:
            prices=await saby.prices(store['point_id'])
            result['available_price_ids']=[p.get('id') for p in prices if isinstance(p,dict)]
            result['selected_price_listed']=str(store['price_list_id']) in {str(x) for x in result['available_price_ids']}
        except Exception as exc:
            result['prices_error']=safe_error(exc)
        try:
            goods=await saby.catalog(store)
            result.update(ok=True,products=len(goods),sample=[{'name':p['name'],'price_rub':p['price']/100,'stock':p['stock']} for p in goods[:5]])
        except Exception as exc:
            result.update(ok=False,error=safe_error(exc))
        yield result
