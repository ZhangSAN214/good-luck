"""网页版数据解析与只读守卫（不开浏览器）。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.mail.owa import (
    block_reason,
    clean_error,
    extract_attachments,
    extract_bodies,
    extract_html,
    extract_items,
    find_items_request,
    get_attachments_request,
    get_items_request,
    html_to_text,
)

from .samples import (
    attachments_response,
    ews_item,
    find_response,
    get_response,
    html_response,
    inline_attachment,
)

OWA = "https://outlook.office.com"


@pytest.mark.parametrize(
    ("url", "method", "headers", "body", "blocked"),
    [
        ("https://login.microsoftonline.com/common/login", "POST", {}, "pw", False),
        ("https://aadcdn.msftauth.net/x.js", "GET", {}, None, False),
        ("https://idp.manchester.ac.uk/sso", "POST", {}, None, False),
        ("https://tracker.example.com/pixel.gif", "GET", {}, None, True),
        ("http://outlook.office.com/mail/", "GET", {}, None, True),
        (f"{OWA}/mail/inbox", "GET", {}, None, False),
        (f"{OWA}/owa/service.svc?action=FindItem&app=Mail", "POST", {}, "{}", False),
        (f"{OWA}/owa/service.svc?action=GetItem", "POST", {}, "{}", False),
        (f"{OWA}/owa/service.svc?action=UpdateItem", "POST", {}, "{}", True),
        (f"{OWA}/owa/service.svc", "POST", {"Action": "DeleteItem"}, "{}", True),
        (f"{OWA}/owa/service.svc", "POST", {"action": "CreateItem"}, "{}", True),
        (f"{OWA}/owa/service.svc?action=MarkAllItemsAsRead", "POST", {}, "{}", True),
        (f"{OWA}/owa/graphql", "POST", {}, '{"query":"query Q { a }"}', False),
        (f"{OWA}/owa/graphql", "POST", {}, '{"query":"mutation M { a }"}', True),
        (f"{OWA}/api/v2.0/me/sendmail", "POST", {}, "{}", True),
        (f"{OWA}/api/v2.0/me/messages/1", "PATCH", {}, "{}", True),
        ("https://outlook.cloud.microsoft/owa/service.svc?action=MoveItem", "POST", {}, "", True),
        ("data:image/png;base64,AAAA", "GET", {}, None, False),
    ],
)
def test_guard_only_lets_login_and_reads_through(url, method, headers, body, blocked):
    assert (block_reason(url, method, headers, body) is not None) is blocked


def test_guard_extra_hosts_for_school_sso():
    assert block_reason("https://sso.other.example/login", "GET")
    assert (
        block_reason("https://sso.other.example/login", "GET", extra_hosts=["other.example"])
        is None
    )


def test_extracts_messages_from_find_item_and_skips_non_messages():
    data = find_response(
        [
            ews_item(1, flagged=True, high=True, read=True),
            {**ews_item(2), "__type": "CalendarItem:#Exchange"},
            {**ews_item(3), "From": {"Mailbox": {"Name": "Someone", "EmailAddress": "/o=X500"}}},
            ews_item(1),  # 重复的只算一次
        ]
    )
    items = extract_items(data)
    assert [i.id for i in items] == ["AAMk/1+=", "AAMk/3+="]
    first = items[0]
    assert (first.subject, first.sender_addr, first.preview) == (
        "Subject 1",
        "tutor@manchester.ac.uk",
        "Hello there",
    )
    assert first.received_at == "2026-10-26T10:00:00+00:00"
    assert first.is_read and first.flagged and first.high and first.attachments
    assert items[1].sender_addr == "" and items[1].sender_name == "Someone"


def test_extracts_lowercase_graph_like_shape():
    data = {
        "value": [
            {
                "id": "g1",
                "subject": "Exam timetable",
                "receivedDateTime": "2026-10-26T11:00:00+01:00",
                "from": {"emailAddress": {"name": "Exams", "address": "Exams@Manchester.ac.uk"}},
                "bodyPreview": "See attached",
                "isRead": False,
                "flag": {"flagStatus": "notFlagged"},
                "importance": "high",
            }
        ]
    }
    [item] = extract_items(data)
    assert item.id == "g1" and item.received_at == "2026-10-26T10:00:00+00:00"
    assert item.sender_addr == "exams@manchester.ac.uk" and item.high and not item.flagged


def test_bodies_text_and_html():
    assert extract_bodies(get_response({"a": "Line 1\r\n\r\n\r\n\r\nLine 2"})) == {
        "a": "Line 1\n\nLine 2"
    }
    html = (
        "<html><head><style>p{}</style></head><body><p>Hi&nbsp;<b>you</b></p>"
        "<script>x()</script>Due 5 Nov</body></html>"
    )
    assert extract_bodies(get_response({"b": html}, "HTML")) == {"b": "Hi you\nDue 5 Nov"}
    assert html_to_text("a<br>b") == "a\nb"


def test_requests_are_read_only_shapes():
    find = find_items_request(50)
    assert find["Body"]["Paging"]["MaxEntriesReturned"] == 50
    assert find["Body"]["ParentFolderIds"][0]["Id"] == "inbox"
    get = get_items_request(["a", "b"])
    assert [x["Id"] for x in get["Body"]["ItemIds"]] == ["a", "b"]
    assert get["Body"]["ItemShape"]["BodyType"] == "Text"
    assert "Update" not in json.dumps([find, get]) and "Delete" not in json.dumps([find, get])


def test_clean_error_drops_url_params_and_truncates():
    text = clean_error("failed https://login.microsoftonline.com/x?code=SECRETCODE&state=1 now")
    assert "SECRETCODE" not in text and "https://login.microsoftonline.com/x" in text
    assert len(clean_error("x" * 500)) == 200


def test_html_bodies_with_inline_image_list():
    data = html_response(
        "AAMk/1+=",
        '<p>Hi</p><img src="cid:image001.png@01DA">',
        [
            inline_attachment("att-1", "image001.png@01DA"),
            inline_attachment("att-2", "doc@x", kind="application/pdf"),
            {"AttachmentId": {"Id": "att-3"}, "ContentType": "image/png"},  # 没有 cid：普通附件
        ],
    )
    [(item_id, (html, images))] = extract_html(data).items()
    assert item_id == "AAMk/1+=" and html.startswith("<p>Hi")
    assert images == [{"id": "att-1", "cid": "image001.png@01DA", "type": "image/png", "size": 10}]
    text = get_response({"m2": "a < b"})  # 只有纯文本的邮件：包成 <pre>
    assert extract_html(text) == {"m2": ("<pre>a &lt; b</pre>", [])}


def test_attachments_and_html_requests_are_read_only_shapes():
    req = get_items_request(["a"], html=True)
    shape = req["Body"]["ItemShape"]
    assert shape["BodyType"] == "HTML"
    assert [p["FieldURI"] for p in shape["AdditionalProperties"]] == ["Body", "Attachments"]
    att = get_attachments_request(["x"])
    assert att["Body"]["__type"] == "GetAttachmentRequest:#Exchange"
    assert extract_attachments(attachments_response({"x": "AAAA"})) == {"x": ("image/png", "AAAA")}
