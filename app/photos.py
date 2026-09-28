"""Bounded photo downloads; never forward the Saby token to another host."""
from io import BytesIO
from urllib.parse import urljoin, urlsplit
from PIL import Image, ImageOps

MAX_BYTES=12*1024*1024
ALLOWED_HOSTS={'api.sbis.ru','disk.sbis.ru','disk.saby.ru'}

def trusted_url(url):
    u=urlsplit(url)
    return u.scheme=='https' and u.netloc in ALLOWED_HOSTS and not u.fragment

async def download(client,url,token):
    for _ in range(5):
        if not trusted_url(url): raise ValueError('Фото: неподдерживаемый адрес или перенаправление')
        headers={'X-SBISAccessToken':token} if urlsplit(url).netloc=='api.sbis.ru' else {}
        async with client.stream('GET',url,headers=headers,follow_redirects=False) as response:
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get('location')
                if not location: raise ValueError('Фото: перенаправление без адреса')
                url=urljoin(url,location);continue
            if response.status_code!=200: return response.status_code,b''
            data=bytearray()
            async for block in response.aiter_bytes():
                data.extend(block)
                if len(data)>MAX_BYTES: raise ValueError('Фото превышает 12 МБ')
            return 200,bytes(data)
    raise ValueError('Фото: слишком много перенаправлений')

def normalize(data):
    try:
        with Image.open(BytesIO(data)) as img:
            if img.width*img.height>25_000_000: raise ValueError('Фото превышает 25 мегапикселей')
            img=ImageOps.exif_transpose(img)
            img.thumbnail((1600,1600))
            output=BytesIO()
            # Always output a browser-supported format, regardless of upstream MIME.
            if img.mode in ('RGBA','LA') or 'transparency' in img.info:
                img.convert('RGBA').save(output,format='PNG')
                return output.getvalue(),'image/png'
            img.convert('RGB').save(output,format='JPEG',quality=88)
            return output.getvalue(),'image/jpeg'
    except Exception as exc:
        raise ValueError('Saby вернул файл, который не удалось прочитать как фото') from exc
