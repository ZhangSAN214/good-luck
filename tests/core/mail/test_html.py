"""图文版正文的清洗：只留排版，脚本和外链一律去掉，自带图片换成 data:，外部图片计数。"""

from __future__ import annotations

from roundtable.core.mail.html import sanitize

PNG = "data:image/png;base64,iVBORw0KGgo="


def test_scripts_events_and_forms_are_removed():
    out = sanitize(
        '<body onload="x()"><p onclick="y()" class="a">Hi <b>there</b></p>'
        "<script>alert(1)</script><script/><noscript>n</noscript>"
        '<form action="https://x.example"><input name="p">pw</form>'
        '<iframe src="https://x.example"></iframe><object data="x"></object>'
        "<svg><title>t</title><script>z()</script></svg>"
        '<meta http-equiv="refresh" content="0;url=https://x.example"><base href="https://x">'
        "<p>after</p></body>"
    )["html"]
    assert out == '<p class="a">Hi <b>there</b></p><p>after</p>'


def test_links_open_in_new_window_and_only_safe_schemes():
    out = sanitize(
        '<a href="javascript:alert(1)">a</a><a href=" https://ok.example/x?a=1&b=2">b</a>'
        '<a href="mailto:t@x.example">c</a><a href="data:text/html,x">d</a>'
    )["html"]
    assert "javascript" not in out and "data:text" not in out
    assert 'href="https://ok.example/x?a=1&amp;b=2"' in out
    assert out.count('target="_blank" rel="noopener noreferrer"') == 4


def test_inline_images_are_embedded_and_remote_ones_counted():
    got = sanitize(
        '<img src="cid:image001.png@01DA"><img src="CID:missing@x">'
        '<img src="http://track.example/p.gif" width="1"><img src="https://cdn.example/a.png">'
        f'<img src="{PNG}"><img src="data:image/svg+xml;base64,AAAA"><img src="file:///c/x.png">',
        {"image001.png@01DA": PNG},
    )
    assert got["embedded"] == 2 and got["remote"] == 2
    html = got["html"]
    assert html.count(PNG) == 2
    assert 'src="https://track.example/p.gif"' in html  # http 改成 https，界面默认仍然拦着
    assert "svg" not in html and "file:" not in html


def test_styles_keep_layout_but_lose_imports_and_remote_urls():
    got = sanitize(
        "<head><style>@import url(https://x.example/a.css); p{color:red;"
        "background:url('https://x.example/b.png')} </style></head>"
        '<p style="width:expression(alert(1));background:url(https://t.example/p.gif)">x</p>'
        f'<div style="background:url({PNG})">y</div>'
    )["html"]
    assert got.startswith("<style>") and "color:red" in got
    assert "@import" not in got and "x.example" not in got and "t.example" not in got
    assert "expression" not in got and PNG in got


def test_text_is_escaped_and_unknown_tags_dropped_but_text_kept():
    out = sanitize("<custom-tag>a &lt;b&gt; <blink>c</blink></custom-tag></style>")["html"]
    assert out == "a &lt;b&gt; c"
