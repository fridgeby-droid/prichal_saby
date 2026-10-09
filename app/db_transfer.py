"""Portable transfer of application tables, with empty-target and maintenance guards."""
import json
import os
from pathlib import Path
from . import database

TABLES={
    'orders':['id','user_id','request_key','data'],
    'messages':['id','order_id','sender','text','created'],
    'outbox':['id','chat','text','order_id','sent','attempts'],
    'replies':['chat','message_id','order_id'],
    'tg_updates':['id'],
    'active_chats':['user_id','order_id'],
    'pickup_cache':['key','data','updated'],
    'support_topics':['order_id','chat','thread_id','state'],
    'support_audit':['id','order_id','sender_id','sender_name','text','created'],
}


def transfer(connect,path,operation):
    if os.getenv('MAINTENANCE_MODE','false').lower()!='true':
        raise ValueError('Включите MAINTENANCE_MODE=true и перезапустите приложение перед переносом')
    with database.single_worker():
        if operation=='export':
            with connect() as c:
                data={table:[dict(r) for r in c.execute('SELECT '+','.join(cols)+' FROM '+table)] for table,cols in TABLES.items()}
            # No overwrite of an existing backup, owner-only permissions from creation.
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f: json.dump({'format':1,'tables':data},f,ensure_ascii=False)
        else:
            payload=json.loads(Path(path).read_text())
            if payload.get('format')!=1 or set(payload.get('tables',{}))!=set(TABLES): raise ValueError('Неподдерживаемый формат копии')
            data=payload['tables']
            with connect() as c:
                if any(c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in TABLES):
                    raise ValueError('Восстановление разрешено только в пустую базу приложения')
                for table,cols in TABLES.items():
                    for row in data[table]:
                        if set(row)!=set(cols): raise ValueError('Неверная структура таблицы '+table)
                        c.execute('INSERT INTO '+table+'('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(row[k] for k in cols))
                if database.postgres():
                    for table in ('messages','outbox','support_audit'):
                        c.execute("SELECT setval(pg_get_serial_sequence(?, 'id'), COALESCE((SELECT MAX(id) FROM "+table+"),1), EXISTS(SELECT 1 FROM "+table+"))",(table,))
    return {t:len(rows) for t,rows in data.items()}
