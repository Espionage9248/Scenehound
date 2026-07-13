from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scenehound.api import AppState, IndexHolder, router
from scenehound.clients.prowlarr import ProwlarrClient
from scenehound.config import (
    Config, IndexerConfig, MatchingConfig, RateLimitConfig, ServiceConfig,
)
from scenehound.models import SceneFingerprint
from scenehound.rate_limiter import TokenBucket
from scenehound.wanted_index import WantedIndex

SCENE = SceneFingerprint(
    scene_id=7,
    site="That Fetish Girl",
    site_aliases=("TFG",),
    date=date(2026, 7, 7),
    title="Latex Worship Session",
    performers=("Jane Doe", "Mary Major"),
)

FEED_MATCHING = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>TFG.26.07.07.Latex.Worship.Session.1080p</title>
      <guid>g-match</guid><link>http://p/dl/1</link>
      <torznab:attr name="category" value="6000"/>
      <torznab:attr name="seeders" value="5"/>
    </item>
    <item>
      <title>Unrelated.Studio.Thing.720p</title>
      <guid>g-nomatch</guid><link>http://p/dl/2</link>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""


def make_config(**overrides) -> Config:
    base = dict(
        whisparr=ServiceConfig("http://w:6969", "wk"),
        prowlarr=ServiceConfig("http://p:9696", "pk"),
        indexers=(IndexerConfig("empornium", 12), IndexerConfig("happyfappy", 15)),
        api_key="shk",
        matching=MatchingConfig(),
        rate_limit=RateLimitConfig(),
        log_level="debug",
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def prowlarr_calls():
    return []


def build_app(prowlarr_calls, store=None, with_index=True, status=200,
              config=None, feed=FEED_MATCHING):
    def handler(request: httpx.Request) -> httpx.Response:
        prowlarr_calls.append(dict(request.url.params))
        return httpx.Response(status, content=feed)

    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = config or make_config()
    holder = IndexHolder()
    if with_index:
        holder.set(WantedIndex([SCENE]))
    state = AppState(
        config=config,
        prowlarr=ProwlarrClient(config.prowlarr.url, config.prowlarr.api_key, hc),
        index_holder=holder,
        buckets={
            i.slug: TokenBucket(config.rate_limit.burst, config.rate_limit.refill_seconds)
            for i in config.indexers
        },
        store=store,
    )
    application = FastAPI()
    application.include_router(router)
    application.state.scenehound = state
    return application


@pytest.fixture
def app(prowlarr_calls):
    return build_app(prowlarr_calls)


@pytest.fixture
def store():
    from scenehound.observe import SessionStore
    return SessionStore(max_sessions=50, max_candidates=200)


@pytest.fixture
def app_with_store(prowlarr_calls, store):
    return build_app(prowlarr_calls, store=store)


@pytest.fixture
def make_app(prowlarr_calls):
    """Builder for tests that need non-default apps (no index / error feeds)."""
    def _make(store=None, with_index=True, status=200, matching=None, feed=FEED_MATCHING):
        config = make_config(matching=matching) if matching is not None else None
        return build_app(prowlarr_calls, store=store, with_index=with_index,
                         status=status, config=config, feed=feed)
    return _make


@pytest.fixture
def client(app):
    return TestClient(app)
