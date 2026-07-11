import httpx
import pytest

from scenehound.clients.prowlarr import ProwlarrClient, ProwlarrError

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel><item>
    <title>A.Release</title><guid>g1</guid><link>http://p/dl/1</link>
    <torznab:attr name="category" value="6000"/>
  </item></channel>
</rss>"""


def make_client(handler):
    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ProwlarrClient("http://p:9696", "pk", hc), hc


async def test_search_hits_indexer_torznab_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=FEED)

    client, hc = make_client(handler)
    async with hc:
        results = await client.search(12, "site 26.07.07", [6000])
    assert "/12/api" in seen["url"]
    assert seen["params"]["t"] == "search"
    assert seen["params"]["q"] == "site 26.07.07"
    assert seen["params"]["cat"] == "6000"
    assert seen["params"]["apikey"] == "pk"
    assert len(results) == 1 and results[0].title == "A.Release"


async def test_rss_fetch_omits_q():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=FEED)

    client, hc = make_client(handler)
    async with hc:
        await client.search(12, None, [6000])
    assert "q" not in seen["params"]


async def test_http_error_raises_prowlarr_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client, hc = make_client(handler)
    async with hc:
        with pytest.raises(ProwlarrError):
            await client.search(12, "x", [6000])
