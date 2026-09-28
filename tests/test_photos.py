import asyncio
from io import BytesIO
import httpx
import pytest
from PIL import Image
from app.photos import normalize,download
from app.products import image_path

def picture(format='BMP'):
    out=BytesIO();Image.new('RGB',(20,10),'red').save(out,format=format);return out.getvalue()

def test_binary_mime_and_bmp_conversion():
    data,mime=normalize(picture())
    assert mime=='image/jpeg'
    with Image.open(BytesIO(data)) as img: assert img.size==(20,10)

def test_html_is_not_image():
    with pytest.raises(ValueError): normalize(b'<html>Login</html>')

@pytest.mark.parametrize('path',['/img?params=abc','retail/img?params=abc','/retail/img?params=abc',{'url':'img?params=abc'}])
def test_link_formats(path): assert image_path(path)=='img?params=abc'

def test_redirect_does_not_forward_token():
    seen=[]
    def handler(r):
        seen.append(r)
        if r.url.host=='api.sbis.ru': return httpx.Response(302,headers={'location':'https://disk.sbis.ru/disk/photo'})
        return httpx.Response(200,content=picture(),headers={'content-type':'application/octet-stream'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            code,data=await download(c,'https://api.sbis.ru/retail/img?params=x','secret')
            assert code==200 and normalize(data)[1]=='image/jpeg'
    asyncio.run(run())
    assert seen[0].headers['X-SBISAccessToken']=='secret'
    assert 'X-SBISAccessToken' not in seen[1].headers

def test_untrusted_redirect_is_blocked():
    seen=[]
    def handler(r):
        seen.append(r)
        return httpx.Response(302,headers={'location':'https://evil.test/photo'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(ValueError): await download(c,'https://api.sbis.ru/retail/img','secret')
    asyncio.run(run());assert len(seen)==1
