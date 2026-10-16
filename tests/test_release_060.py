import asyncio,json,time,uuid
import pytest
from app.products import line_item,rules
from app.integrations import YooKassa,IntegrationError
from app import database,object_storage,support
from app.catalog_cache import CatalogCache


def test_litre_amount_stock_and_steps():
    p={'unit':'л','price':19999,'stock':1.5}
    assert rules('Л.')['step_qty']==500
    item=line_item(p,1500)
    assert item['qty']==1.5 and item['line_amount']==29999 and item['volume']
    for selection in [0,500,1250,2000]:
        with pytest.raises(ValueError): line_item(p,selection)
    with pytest.raises(ValueError): line_item({**p,'stock':100},30500)


def test_shared_account_and_frozen_shop(monkeypatch):
    y=YooKassa()
    monkeypatch.setenv('YOOKASSA_SHOP_ID','101');monkeypatch.setenv('YOOKASSA_SECRET_KEY','test_shared')
    monkeypatch.setenv('YOOKASSA_SHOP_ID_2','202');monkeypatch.setenv('YOOKASSA_SECRET_KEY_2','test_unused')
    for sid in ('1','2','3'):
        assert y.credentials({'store_id':sid})==('101','test_shared')
    order={'store_id':'1','payment_shop_id':'101'}
    monkeypatch.setenv('YOOKASSA_SHOP_ID','303')
    with pytest.raises(IntegrationError): y.credentials(order)
    monkeypatch.setenv('YOOKASSA_ARCHIVE_SHOP_ID_101','101');monkeypatch.setenv('YOOKASSA_ARCHIVE_SECRET_KEY_101','test_old')
    assert y.credentials(order)==('101','test_old')
    assert y.credentials({'store_id':'2','payment_shop_id':'202'})==('202','test_unused')
    monkeypatch.delenv('YOOKASSA_SECRET_KEY')
    with pytest.raises(IntegrationError): y.credentials({'store_id':'2'})


def test_provider_receives_selected_credentials(monkeypatch):
    import httpx
    monkeypatch.setenv('YOOKASSA_SHOP_ID','101');monkeypatch.setenv('YOOKASSA_SECRET_KEY','test_101')
    real=httpx.AsyncClient;seen=[]
    def handle(request):
        seen.append(request.headers['authorization']);return httpx.Response(200,json={'id':'p'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    assert asyncio.run(YooKassa().get('p',{'store_id':'1'}))=={'id':'p'}
    import base64
    assert seen==['Basic '+base64.b64encode(b'101:test_101').decode()]


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'db';database.init(path)
    return lambda:database.connect(path)


def test_support_one_topic_restart_and_uncertain(db,monkeypatch):
    monkeypatch.setenv('SUPPORT_GROUP_ID','-100123')
    calls=[]
    async def tg(method,body):calls.append(method);return {'message_thread_id':7}
    o={'id':'order1'}
    assert asyncio.run(support.thread(db,tg,o,'Магазин'))==7
    assert asyncio.run(support.thread(db,tg,o,'Магазин'))==7
    assert calls==['createForumTopic']
    with db() as c:c.execute("UPDATE support_topics SET state='closed'")
    asyncio.run(support.thread(db,tg,o,'Магазин'))
    assert calls[-1]=='reopenForumTopic'
    async def timeout(*a):raise TimeoutError()
    with pytest.raises(TimeoutError):asyncio.run(support.thread(db,timeout,{'id':'uncertain'},'Магазин'))
    with pytest.raises(IntegrationError):asyncio.run(support.thread(db,tg,{'id':'uncertain'},'Магазин'))
    assert calls.count('createForumTopic')==1


def test_group_member_reply_routing_and_audit(db,monkeypatch):
    monkeypatch.setenv('SUPPORT_GROUP_ID','-100123')
    o={'id':'o','user_id':'777','store_id':'2'}
    with db() as c:
        c.execute('INSERT INTO orders VALUES(?,?,?,?)',('o','777','r',json.dumps(o)))
        c.execute("INSERT INTO support_topics VALUES('o','-100123',7,'open')")
        c.execute("INSERT INTO replies VALUES('-100123',23,'o')")
    m={'chat':{'id':-100123,'type':'supergroup'},'message_thread_id':7,'from':{'id':999,'first_name':'Иван'},'reply_to_message':{'message_id':23}}
    assert not support.reply(db,{**m,'message_thread_id':8},'wrong topic',1)
    assert not support.reply(db,{**m,'message_thread_id':1},'general',1)
    assert not support.reply(db,{**m,'chat':{'id':-100124,'type':'supergroup'}},'wrong group',1)
    assert support.reply(db,{**m,'reply_to_message':{}},'Ответ',1)
    assert support.reply(db,m,'Ответ',1)
    with db() as c:
        assert c.execute('SELECT text FROM messages').fetchone()[0]=='Ответ'
        assert c.execute('SELECT chat FROM outbox').fetchone()[0]=='777'
        assert c.execute('SELECT sender_id FROM support_audit').fetchone()[0]=='999'
        assert c.execute('SELECT id FROM tg_updates').fetchone()[0]==1


def test_s3_cache_transfer_preserves_photo_and_does_not_redownload(db,tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_STORAGE','s3');monkeypatch.setenv('S3_BUCKET','photos')
    class Saby:
        async def call(self,*a,**kw):raise AssertionError('Initial local photo must be reused')
    cache=CatalogCache(db,Saby(),tmp_path/'photos');cache.photo_dir.mkdir()
    key='a'*64;(cache.photo_dir/(key+'.jpg')).write_bytes(b'jpeg')
    calls=[]
    def put(key,content,mime):
        calls.append(content);return {'storage':object_storage.identity(),'object_key':key+'.jpg','mime':mime}
    monkeypatch.setattr(object_storage,'put',put)
    asyncio.run(cache.save_photo(key,'source'));asyncio.run(cache.save_photo(key,'source'))
    assert calls==[b'jpeg'] and cache.read('photo-s3:'+key)[0]['mime']=='image/jpeg'
    monkeypatch.setenv('S3_PUBLIC_BASE_URL','https://photos.example/bucket')
    assert object_storage.url(cache.read('photo-s3:'+key)[0])=='https://photos.example/bucket/'+key+'.jpg'


def test_s3_failure_keeps_previous_metadata(db,tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_STORAGE','s3')
    class Saby:
        async def call(self,*a,**kw):return b'new','image/png'
    cache=CatalogCache(db,Saby(),tmp_path)
    key='b'*64;old={'object_key':'old.jpg','storage':object_storage.identity()}
    with db() as c:cache.write(c,'photo-s3:'+key,old,0)
    def fail(*a):raise ConnectionError()
    monkeypatch.setattr(object_storage,'put',fail)
    with pytest.raises(ConnectionError):asyncio.run(cache.save_photo(key,'source'))
    assert cache.read('photo-s3:'+key)[0]==old


def test_transfer_roundtrip_empty_guard_and_atomic_rollback(db,tmp_path,monkeypatch):
    from app.db_transfer import transfer
    monkeypatch.setenv('MAINTENANCE_MODE','true')
    with db() as c:c.execute('INSERT INTO orders VALUES(?,?,?,?)',('o','u','r','{}'))
    path=tmp_path/'backup.json';transfer(db,path,'export')
    target=tmp_path/'target.db';database.init(target);connect=lambda:database.connect(target)
    transfer(connect,path,'import')
    with connect() as c:assert c.execute('SELECT id FROM orders').fetchone()[0]=='o'
    with pytest.raises(ValueError):transfer(connect,path,'import')
    bad=json.loads(path.read_text());bad['tables']['messages']=[{'invalid':1}];badpath=tmp_path/'bad.json';badpath.write_text(json.dumps(bad))
    other=tmp_path/'other.db';database.init(other);connect2=lambda:database.connect(other)
    with pytest.raises(ValueError):transfer(connect2,badpath,'import')
    with connect2() as c:assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0


def test_actual_s3_client_presigning_and_upload(monkeypatch):
    from botocore.stub import Stubber
    from urllib.parse import urlsplit,parse_qs
    for k,v in {'PHOTO_STORAGE':'s3','S3_ENDPOINT_URL':'https://s3.example.test','S3_REGION':'ru-1','S3_BUCKET':'pickup-test','S3_ACCESS_KEY_ID':'test-access','S3_SECRET_ACCESS_KEY':'test-secret','S3_PUBLIC_BASE_URL':''}.items():monkeypatch.setenv(k,v)
    client=object_storage.client();stub=Stubber(client)
    monkeypatch.setattr(object_storage,'client',lambda:client)
    stub.add_response('put_object',{}, {'Bucket':'pickup-test','Key':'pickup/photos/abc.jpg','Body':b'jpg','ContentType':'image/jpeg','CacheControl':'public, max-age=3600'})
    with stub:meta=object_storage.put('abc',b'jpg','image/jpeg')
    link=object_storage.url(meta);parsed=urlsplit(link)
    assert parsed.path=='/pickup-test/pickup/photos/abc.jpg'
    assert parse_qs(parsed.query)['X-Amz-Algorithm']==['AWS4-HMAC-SHA256']
    assert 'test-secret' not in link


def test_forum_outbox_uses_thread_and_maps_real_group(db,monkeypatch):
    from app import server as s
    monkeypatch.setenv('SUPPORT_GROUP_ID','-100123');monkeypatch.setenv('BOT_TOKEN','dummy')
    monkeypatch.setattr(s,'connect',db);monkeypatch.setattr(s,'all_orders',lambda:[])
    monkeypatch.setattr(s,'get_order',lambda oid:{'id':oid,'store_id':'store'})
    monkeypatch.setattr(s,'store_for',lambda sid:{'name':'Магазин'})
    calls=[]
    async def tg(method,body):
        calls.append((method,body));return {'message_thread_id':7} if method=='createForumTopic' else {'message_id':99}
    monkeypatch.setattr(s,'telegram',tg)
    s.queue(support.recipient(),'Вопрос','o')
    asyncio.run(s.tick())
    assert 'Магазин:</b>' in calls[1][1]['text']
    assert calls[2]==('sendMessage',{'chat_id':'-100123','text':'Вопрос','message_thread_id':7,'parse_mode':'HTML'})
    with db() as c:
        assert c.execute('SELECT chat FROM replies').fetchone()[0]=='-100123'
        assert c.execute('SELECT sent FROM outbox').fetchone()[0]==1


def test_payment_rejects_other_recipient(monkeypatch):
    from app import server as s
    o={'id':'o','payment_id':'p','payment_shop_id':'101','amount':100,'payment_status':'pending'}
    p={'id':'p','metadata':{'order_id':'o'},'amount':{'value':'1.00','currency':'RUB'},'test':True,'status':'succeeded','recipient':{'account_id':'202'}}
    with pytest.raises(s.HTTPException) as exc:asyncio.run(s.reconcile_payment(o,p))
    assert exc.value.status_code==400
