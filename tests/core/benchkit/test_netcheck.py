"""联网自检：成品数据和调研接口各试一次；参数填样例；不通的写清原因。网络是假的。"""

from __future__ import annotations

from roundtable.core.benchkit.netcheck import RESEARCH, fill, net_check
from roundtable.core.config.schema import BenchNetService


def test_fill_uses_probe_then_samples():
    url = "https://x/s?name={q}&lat={lat}&other={zzz}"
    assert fill(url, {}) == "https://x/s?name=%E4%B8%9C%E4%BA%AC&lat=35.68&other=1"
    assert fill(url, {"q": "Paris"}).startswith("https://x/s?name=Paris&")


async def test_net_check_reports_each_item():
    services = [
        BenchNetService(
            name="tiles", use="底图", url="https://t/{z}/{x}/{y}.png", source="OSM", kind="tiles"
        ),
        BenchNetService(name="fx", use="汇率", url="https://fx/?b={from}", source="ECB"),
        BenchNetService(name="wx", use="天气", url="https://wx/?a={lat}", source="Meteo"),
    ]
    seen = []

    async def fetch(url):
        seen.append(url)
        if url.startswith("https://fx"):
            return 429
        if url.startswith("https://wx"):
            raise TimeoutError
        return 200

    items = await net_check(services, fetch)
    assert len(items) == 3 + len(RESEARCH)
    by = {x["name"]: x for x in items}
    assert by["tiles"]["ok"] and by["tiles"]["group"] == "成品数据"
    assert "https://t/1/1/1.png" in seen
    assert not by["fx"]["ok"] and "限流" in by["fx"]["detail"]
    assert not by["wx"]["ok"] and "TimeoutError" in by["wx"]["detail"]
    assert by["github"]["group"] == "调研"
