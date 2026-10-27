"""邮件栏的本机规则：「重要」判断与日期提取。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from roundtable.core.mail.rules import MailRules, extract_dates, judge

RECEIVED = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


def mail(**kw) -> dict:
    return {"sender_addr": "someone@gmail.example", "subject": "", "preview": "", **kw}


def test_school_senders_count_but_classmates_do_not():
    rules = MailRules()
    assert judge(mail(sender_addr="tutor@manchester.ac.uk"), rules) == ["学校发件人"]
    assert judge(mail(sender_addr="exams@cs.manchester.ac.uk"), rules) == ["学校发件人"]
    assert judge(mail(sender_addr="mate@student.manchester.ac.uk"), rules) == []
    assert judge(mail(sender_addr="x@notmanchester.ac.uk"), rules) == []


def test_keywords_are_whole_words_and_flags_count():
    rules = MailRules()
    assert judge(mail(subject="Coursework DEADLINE moved"), rules) == [
        "关键词：deadline、coursework"
    ]
    assert judge(mail(subject="Your overdue library book"), rules) == []
    assert judge(mail(preview="下周三考试"), rules) == ["关键词：考试"]
    assert judge(mail(flagged=True, high=True), rules) == ["你插了旗标", "发件人标为重要"]
    assert judge(mail(flagged=True), MailRules(use_flags=False)) == []


def test_own_senders_win_over_domains():
    rules = MailRules(senders=["Mum@Home.example"])
    assert rules.senders == ["mum@home.example"]
    assert judge(mail(sender_addr="mum@home.example"), rules) == ["你加的发件人"]


def test_rules_are_cleaned_and_bounded():
    rules = MailRules(keywords=[" Exam ", "exam", "", "@x"], domains=["@uni.example"])
    assert rules.keywords == ["exam", "x"] and rules.domains == ["uni.example"]
    with pytest.raises(ValueError):
        MailRules(keywords=[str(i) for i in range(101)])
    with pytest.raises(ValueError):
        MailRules(color="red")


@pytest.mark.parametrize(
    ("text", "due"),
    [
        ("Coursework is due 5 Nov 2026 at 23:59.", "2026-11-05T23:59"),
        ("Please submit by 5th November, 5pm.", "2026-11-05T17:00"),
        ("Deadline: Nov 12, 2026 11:59pm", "2026-11-12T23:59"),
        ("Exam on 2026-12-08 09:30 in the main hall", "2026-12-08T09:30"),
        ("Submit before 03/11/2026 please", "2026-11-03"),
        ("作业 11月20日 23:59 截止", "2026-11-20T23:59"),
        ("Assessment due 10 Jan", "2027-01-10"),
    ],
)
def test_extracts_deadlines(text, due):
    assert [d["due"] for d in extract_dates(text, RECEIVED)] == [due]


def test_ignores_dates_without_trigger_past_dates_and_bad_numbers():
    assert extract_dates("Lecture recording from 5 Nov 2026 is up.", RECEIVED) == []
    assert extract_dates("Deadline was 1 Oct 2026.", RECEIVED) == []
    assert extract_dates("Due Nov 2026 sometime", RECEIVED) == []
    assert extract_dates("Due 31/02/2027", RECEIVED) == []
    many = "Due 1 Nov. Due 2 Nov. Due 3 Nov. Due 4 Nov."
    assert len(extract_dates(many, RECEIVED)) == 3
    assert extract_dates("Due 5 Nov 2026 and also due 5 Nov 2026", RECEIVED)[0]["snippet"]
