"""Quantity rules: the cart uses grams for weight goods, pieces otherwise."""
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

class PlainText(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip+=1
        if tag in ('p','br','div','li'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip: self.parts.append(data)

def description(value):
    parser=PlainText(); parser.feed(str(value or ''))
    return '\n'.join(x.strip() for x in ''.join(parser.parts).splitlines() if x.strip())[:10000]

def image_path(value):
    if not isinstance(value,str): return None
    url=urlsplit(urljoin('https://api.sbis.ru/retail/',value))
    if url.scheme!='https' or url.netloc!='api.sbis.ru' or url.path!='/retail/img' or url.fragment: return None
    return 'img'+('?' + url.query if url.query else '')

def rules(unit):
    unit=unit.strip().lower().rstrip('.')
    if unit in ('кг','kg','килограмм'): return {'weighted':True,'min_qty':150,'step_qty':50,'divisor':1000}
    if unit in ('г','гр','g','грамм'): return {'weighted':True,'min_qty':150,'step_qty':50,'divisor':1}
    if unit in ('шт','штука','упак','уп'): return {'weighted':False,'min_qty':1,'step_qty':1,'divisor':1}
    return None

def line_item(product, selection):
    r=rules(product['unit'])
    if not r: raise ValueError('Неподдерживаемая единица товара')
    if selection < r['min_qty'] or (selection-r['min_qty']) % r['step_qty']:
        raise ValueError('Вес: минимум 150 г, шаг 50 г. Штучные товары: целое количество.')
    if selection > (30000 if r['weighted'] else 30): raise ValueError('Слишком большое количество')
    qty=Decimal(selection)/r['divisor']
    if product['stock'] is not None and qty>Decimal(str(product['stock'])): raise ValueError('Недостаточно товара в наличии')
    amount=int((Decimal(product['price'])*qty).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return {**product,'qty':float(qty),'selection':selection,'line_amount':amount,'weighted':r['weighted']}
