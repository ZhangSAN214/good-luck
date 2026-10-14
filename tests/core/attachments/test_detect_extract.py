from __future__ import annotations

import io
import zipfile

import pytest

from roundtable.core.attachments import (
    FileStore,
    UploadError,
    decode_text,
    detect,
    display_name,
    extract_docx,
    extract_pdf,
)
from roundtable.core.config import load_config

from .samples import JPG, MP3, PNG, WAV, docx, pdf

RULES = load_config().roundtable.uploads


@pytest.mark.parametrize(
    ("name", "data", "kind", "mime"),
    [
        ("a.png", PNG, "image", "image/png"),
        ("B.JPG", JPG, "image", "image/jpeg"),
        ("x.pdf", pdf("hello"), "pdf", "application/pdf"),
        ("x.docx", docx("hi"), "docx", None),
        ("n.txt", "你好".encode(), "text", "text/plain"),
        ("n.csv", b"a,b\n1,2\n", "text", "text/csv"),
        ("v.mp3", MP3, "audio", "audio/mpeg"),
        ("v.wav", WAV, "audio", "audio/wav"),
    ],
)
def test_detect_by_extension_and_header(name, data, kind, mime):
    t = detect(name, data)
    assert t.kind == kind and (mime is None or t.mime == mime)


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("fake.png", b"MZ\x90\x00" + b"\x00" * 64, "不符"),  # 可执行文件改成 .png
        ("fake.pdf", PNG, "不符"),
        ("fake.docx", b"PK\x03\x04" + b"\x00" * 64, "不符"),  # 普通 zip 不是 Word
        ("bin.txt", b"\x00\x01\x02binary", "不符"),
        ("run.exe", b"MZ", "不支持"),
        ("old.doc", b"\xd0\xcf\x11\xe0", ".docx"),
        ("noext", b"abc", "不支持"),
        ("empty.txt", b"", "空"),
    ],
)
def test_detect_rejects(name, data, message):
    with pytest.raises(UploadError, match=message):
        detect(name, data)


def test_display_name_never_carries_a_path():
    assert display_name("../../etc/passwd") == "passwd"
    assert display_name("C:\\Users\\me\\.env") == ".env"
    assert not set('<>"') & set(display_name('<b>"x".png'))
    assert display_name("") == "未命名"
    assert len(display_name("a" * 500 + ".png")) <= 120


def test_pdf_text_and_pages():
    first, second = "Page one: find the maximum of f(x)", "Second page: show every step"
    out = extract_pdf(pdf(first, second), RULES)
    assert out.pages == 2 and first in out.text and "[第 2 页]" in out.text
    assert out.warnings == ()


def test_scanned_pdf_warns():
    out = extract_pdf(pdf("", ""), RULES)
    assert out.pages == 2 and any("扫描件" in w for w in out.warnings)


def test_pdf_page_limit_and_corrupt():
    small = RULES.model_copy(update={"max_pdf_pages": 1})
    with pytest.raises(UploadError, match="超过上限"):
        extract_pdf(pdf("a", "b"), small)
    with pytest.raises(UploadError, match="损坏"):
        extract_pdf(b"%PDF-1.4\n garbage", RULES)


def test_encrypted_pdf_rejected():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(pdf("secret"))).pages:
        writer.add_page(page)
    writer.encrypt("pw")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(UploadError, match="加密"):
        extract_pdf(out.getvalue(), RULES)


def test_docx_paragraphs_and_tables():
    out = extract_docx(docx("第一段", "第二段", table=[["x", "y"], ["1", "2"]]), RULES)
    assert "第一段\n第二段" in out.text and "x | y" in out.text and "1 | 2" in out.text


def test_corrupt_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "not xml")
    with pytest.raises(UploadError, match="损坏"):
        extract_docx(buf.getvalue(), RULES)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gbk"])
def test_text_encodings(encoding):
    text = "函数 f(x)=x^2 在区间上的最大值是多少？请写出完整的推导过程，并说明理由。" * 3
    out = decode_text(text.encode(encoding), RULES)
    assert out.text == text


def test_long_text_truncated_with_note():
    rules = RULES.model_copy(update={"max_text_chars": 1000})
    out = decode_text(("长" * 5000).encode(), rules)
    assert len(out.text) < 1100 and "截断" in out.text and out.warnings


def test_store_hash_names_and_no_traversal(tmp_path):
    store = FileStore(tmp_path)
    key = store.save(PNG, "png")
    assert (tmp_path / key).read_bytes() == PNG and store.load(key) == PNG
    assert store.save(PNG, "png") == key  # 相同内容只存一份
    for bad in ("../x.png", "/etc/passwd", "a" * 64 + ".png/../x", key.replace(".png", "")):
        with pytest.raises(ValueError):
            store.load(bad)
    memory = FileStore(None)
    assert memory.load(memory.save(b"abc", "txt")) == b"abc"
