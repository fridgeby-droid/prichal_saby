"""Durable order-to-forum mapping; creation timeouts require operator reconciliation."""
import os
import time
from html import escape
from .integrations import IntegrationError


def group(): return os.getenv('SUPPORT_GROUP_ID','').strip()
def recipient(): return 'support:'+group() if group() else ''


async def thread(connect, telegram, order, store_name):
    chat=group()
    with connect() as c:
        row=c.execute('SELECT * FROM support_topics WHERE order_id=?',(order['id'],)).fetchone()
        if not row:
            c.execute('INSERT INTO support_topics(order_id,chat,state) VALUES(?,?,?)',(order['id'],chat,'creating'))
    if row:
        if row['chat']!=chat: raise IntegrationError('Группа обращения изменена: нужна сверка темы')
        if not row['thread_id']: raise IntegrationError('Неопределённое создание темы: нужна сверка')
        if row['state']=='closed':
            await telegram('reopenForumTopic',{'chat_id':chat,'message_thread_id':row['thread_id']})
            with connect() as c: c.execute("UPDATE support_topics SET state='open' WHERE order_id=?",(order['id'],))
        return row['thread_id']
    # Marked before external call: do not create duplicates after an uncertain response.
    result=await telegram('createForumTopic',{'chat_id':chat,'name':(store_name+' · Заказ №'+order['id'][:8])[:128]})
    tid=result['message_thread_id']
    with connect() as c: c.execute("UPDATE support_topics SET thread_id=?,state='open' WHERE order_id=?",(tid,order['id']))
    return tid


def reply(connect, message, text, update_id, store_name=None):
    """Route ordinary seller text by the durable order topic mapping."""
    chat=str(message.get('chat',{}).get('id',''))
    if chat!=group() or message.get('chat',{}).get('type')!='supergroup': return False
    author=message.get('from',{})
    # Anonymous admin posts have no reliable employee identity; ask them to disable anonymity.
    if not author.get('id') or author.get('is_bot') or message.get('sender_chat'): return False
    tid=message.get('message_thread_id')
    if not tid or tid==1 or not text or not text.strip() or text.startswith('/'): return False
    with connect() as c:
        if c.execute('SELECT 1 FROM tg_updates WHERE id=?',(update_id,)).fetchone(): return True
        row=c.execute('SELECT t.order_id,o.data FROM support_topics t JOIN orders o ON o.id=t.order_id WHERE t.chat=? AND t.thread_id=?',
                      (chat,tid)).fetchone()
        if not row: return False
        import json
        order=json.loads(row['data']);oid=row['order_id'];now=time.time()
        c.execute('INSERT INTO messages(order_id,sender,text,created) VALUES(?,?,?,?)',(oid,'store',text,now))
        name=' '.join(str(author.get(k,'')) for k in ('first_name','last_name')).strip()
        c.execute('INSERT INTO support_audit(order_id,sender_id,sender_name,text,created) VALUES(?,?,?,?,?)',(oid,str(author['id']),name,text,now))
        c.execute('INSERT INTO outbox(chat,text,order_id) VALUES(?,?,?)',(order['user_id'],f"📍 {store_name(order) if store_name else order.get('store_name', 'Магазин')} · Заказ №{oid[:8].upper()}\n{text}",oid))
        c.execute('INSERT INTO tg_updates VALUES(?)',(update_id,))
    return True


def customer_text(order, text):
    return f"💬 <b>{escape(order.get('name') or 'Покупатель')} · Заказ №{escape(order['id'][:8].upper())}</b>\n\n{escape(text)}"


async def introduction(connect, telegram, order, store_name, tid, status_label):
    key='support-intro:'+order['id']
    with connect() as c:
        if c.execute('SELECT 1 FROM pickup_cache WHERE key=?',(key,)).fetchone(): return
    icons={'accepted':'✅','collecting':'📦','ready':'🛍','completed':'🏁','canceled':'❌'}
    text=(f"🛍 <b>Заказ №{escape(order['id'][:8].upper())}</b>\n"
          f"📍 <b>Магазин:</b> {escape(store_name)}\n"
          f"👤 <b>Покупатель:</b> {escape(order.get('name') or 'Покупатель')}\n"
          f"{icons.get(order.get('status'),'🕓')} <b>Статус:</b> {escape(status_label)}")
    await telegram('sendMessage',{'chat_id':group(),'message_thread_id':tid,'text':text,'parse_mode':'HTML'})
    with connect() as c:
        c.execute('INSERT INTO pickup_cache(key,data,updated) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING',(key,'true',time.time()))
