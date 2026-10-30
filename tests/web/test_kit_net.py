"""零件库联网：真实地图底图、点地点联动别的窗口、ctx.net 取公开数据。

真浏览器，网络请求换成假的。
"""

from __future__ import annotations

import base64
import json

from roundtable.core.benchkit.quality import _serve
from roundtable.core.benchkit.spec import normalize, write_shell

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
NET = [
    {
        "name": "tiles",
        "use": "底图",
        "url": "https://t.example/{z}/{x}/{y}.png",
        "source": "© 示例地图",
        "kind": "tiles",
        "ttl": 0,
    },
    {
        "name": "weather",
        "use": "天气",
        "url": "https://w.example/f?lat={lat}&lng={lng}",
        "source": "示例天气",
        "ttl": 60,
    },
    {"name": "fx", "use": "汇率", "url": "https://down.example/fx?to={to}", "source": "示例汇率"},
]
MAP = """export default (el, ctx) => {
  const pts = [{ name: '西湖', lat: 30.25, lng: 120.15 },
    { name: '灵隐寺', lat: 30.24, lng: 120.10 }];
  const onPick = (i, p) => ctx.state.set('picked', p.name);
  ctx.kit.mapFrame(el, { points: pts, height: 320, onPick });
};"""
SIDE = """export default async (el, ctx) => {
  el.innerHTML = '<p class="pick">还没选</p><p class="wx">…</p><p class="fx">…</p>';
  const pick = el.querySelector('.pick');
  ctx.state.on('picked', (v) => { pick.textContent = v ? '已选 ' + v : '还没选'; }, '');
  const w = await ctx.net.get('weather', { lat: 30.25, lng: 120.15 });
  const tag = w && !w.live ? ' · 参考' : '';
  el.querySelector('.wx').textContent = w ? w.data.t + '° · ' + w.source + tag : '拿不到';
  const f = await ctx.net.get('fx', { to: 'JPY' });
  el.querySelector('.fx').textContent = f ? '有' : '参考数据';
};"""


def test_map_tiles_pick_linkage_and_public_data(browser, tmp_path):
    spec = normalize(
        {
            "title": "旅行台",
            "hero": "map",
            "windows": [
                {"id": "map", "title": "地图", "span": 8, "icon": "map"},
                {"id": "side", "title": "详情", "span": 4, "icon": "info"},
            ],
        },
        max_windows=8,
    )
    index = write_shell(tmp_path, spec, "n1", net=NET)
    (tmp_path / "windows" / "map.js").write_text(MAP, encoding="utf-8")
    (tmp_path / "windows" / "side.js").write_text(SIDE, encoding="utf-8")
    calls = []
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.route("https://t.example/**", lambda r: r.fulfill(body=PNG, content_type="image/png"))

    def weather(route):
        calls.append(route.request.url)
        route.fulfill(body=json.dumps({"t": 12}), content_type="application/json")

    page.route("https://w.example/**", weather)
    page.route("https://down.example/**", lambda r: r.abort())
    with _serve(index.parent) as base:
        page.goto(f"{base}/index.html")
        page.locator(".fx", has_text="参考数据").wait_for(timeout=10000)
        assert page.locator(".wx").text_content() == "12° · 示例天气"
        assert calls == ["https://w.example/f?lat=30.25&lng=120.15"]
        tiles = page.locator(".k-map-tiles img")
        assert tiles.count() > 0
        assert "/t.example/" in tiles.first.get_attribute("src")
        assert page.locator(".k-map-credit").text_content() == "© 示例地图"
        assert page.locator("section.k-hero [data-window=map]").count() == 1
        # 点地图上的地点：别的窗口跟着变，地图上把它标出来
        page.locator('.k-pin[aria-label="2. 灵隐寺"]').click()
        page.locator(".pick", has_text="已选 灵隐寺").wait_for()
        assert page.locator(".k-pin.k-on").get_attribute("aria-label") == "2. 灵隐寺"
        # 再打开一次：天气走本机缓存，不再联网
        page.reload()
        page.locator(".wx", has_text="12°").wait_for()
        assert len(calls) == 1
    page.close()
