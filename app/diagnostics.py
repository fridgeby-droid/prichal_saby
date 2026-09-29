"""Bounded operator diagnostics without tokens or customer contact details."""
import os
import re


def safe_error(exc, order=None):
    text=str(exc)
    for key in ('BOT_TOKEN','DATABASE_URL','SABY_CLIENT_ID','SABY_APP_SECRET','SABY_SECRET_KEY','YOOKASSA_SECRET_KEY','TELEGRAM_WEBHOOK_SECRET'):
        value=os.getenv(key,'')
        if value: text=text.replace(value,'[скрыто]')
    for key in ('name','phone','address','user_id'):
        value=str((order or {}).get(key) or '')
        if value: text=text.replace(value,'[скрыто]')
    text=re.sub(r'https?://[^\s<>"\']+', '[адрес скрыт]',text)
    text=re.sub(r'\+?\d[\d ()-]{9,}\d','[номер скрыт]',text)
    return {'type':type(exc).__name__,'message':text[:1200]}
