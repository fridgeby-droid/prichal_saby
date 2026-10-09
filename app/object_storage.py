"""S3-compatible photo storage. Credentials never leave the backend."""
import os
from urllib.parse import quote


def enabled(): return os.getenv('PHOTO_STORAGE','local').lower()=='s3'


def validate():
    mode=os.getenv('PHOTO_STORAGE','local').lower()
    if mode not in ('local','s3'): raise ValueError('PHOTO_STORAGE: local или s3')
    if not enabled(): return
    for key in ('S3_ENDPOINT_URL','S3_REGION','S3_BUCKET','S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY'):
        if not os.getenv(key,'').strip(): raise ValueError('Заполните '+key)
    if not os.environ['S3_ENDPOINT_URL'].startswith('https://'): raise ValueError('S3_ENDPOINT_URL: нужен HTTPS')
    if os.getenv('S3_PUBLIC_BASE_URL') and not os.environ['S3_PUBLIC_BASE_URL'].startswith('https://'):
        raise ValueError('S3_PUBLIC_BASE_URL: нужен HTTPS')


def client():
    import boto3
    from botocore.config import Config
    validate()
    return boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT_URL'],region_name=os.environ['S3_REGION'],
        aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=10,read_timeout=25,
                      retries={'max_attempts':2},request_checksum_calculation='when_required',response_checksum_validation='when_required'))


def identity():
    return [os.getenv('S3_ENDPOINT_URL','').rstrip('/'),os.getenv('S3_BUCKET',''),os.getenv('S3_PREFIX','pickup/photos').strip('/')]


def put(key,content,mime):
    object_key='/'.join(filter(None,[identity()[2],key+('.png' if mime=='image/png' else '.jpg')]))
    client().put_object(Bucket=os.environ['S3_BUCKET'],Key=object_key,Body=content,ContentType=mime,CacheControl='public, max-age=3600')
    return {'object_key':object_key,'mime':mime,'storage':identity()}


def url(meta):
    public=os.getenv('S3_PUBLIC_BASE_URL','').rstrip('/')
    if public: return public+'/'+quote(meta['object_key'],safe='/')
    return client().generate_presigned_url('get_object',Params={'Bucket':os.environ['S3_BUCKET'],'Key':meta['object_key']},ExpiresIn=3600)


def probe():
    import uuid
    key='/'.join(filter(None,[identity()[2],'check-'+uuid.uuid4().hex]))
    s3=client();bucket=os.environ['S3_BUCKET'];created=False
    try:
        s3.put_object(Bucket=bucket,Key=key,Body=b'pickup-storage-check');created=True
        response=s3.get_object(Bucket=bucket,Key=key)
        try: assert response['Body'].read()==b'pickup-storage-check'
        finally: response['Body'].close()
    finally:
        if created: s3.delete_object(Bucket=bucket,Key=key)
    return {'photos':'s3','read_write_delete':True,'bucket':bucket}
