import asyncio
import pytest
from app.integrations import Saby,IntegrationError
from app.diagnostics import catalog_checks

@pytest.mark.parametrize('response',[{'nomenclatures':{},'outcome':{'hasMore':False}},{'nomenclatures':None},{},None,'unexpected'])
def test_unknown_catalog_does_not_become_empty(response):
    api=Saby()
    async def call(*a,**k):return response
    api.call=call
    with pytest.raises(IntegrationError,match='point_id'):
        asyncio.run(api.catalog({'point_id':37462,'price_list_id':66}))

def test_continue_after_failed_point():
    class Fake:
        async def configured_stores(self):return [{'name':'A','point_id':1,'price_list_id':66},{'name':'B','point_id':2,'price_list_id':66}]
        async def prices(self,p):return [{'id':66}]
        async def catalog(self,s):
            if s['point_id']==1:raise IntegrationError('Unexpected shape')
            return [{'name':'item','price':10000,'stock':3}]
    async def run():return [r async for r in catalog_checks(Fake())]
    result=asyncio.run(run())
    assert len(result)==2
    assert result[0]['ok'] is False
    assert result[1]['ok'] is True and result[1]['products']==1
    assert all(r['selected_price_listed'] for r in result)
