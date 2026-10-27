"""假的网页版数据（结构仿照 Outlook 网页版的 JSON）。"""

from __future__ import annotations


def ews_item(i: int, *, when: str = "2026-10-26T10:00:00Z", **kw) -> dict:
    return {
        "__type": "Message:#Exchange",
        "ItemId": {"__type": "ItemId:#Exchange", "Id": f"AAMk/{i}+=", "ChangeKey": "x"},
        "Subject": kw.get("subject", f"Subject {i}"),
        "DateTimeReceived": when,
        "From": {
            "Mailbox": {
                "Name": kw.get("name", "Dr Tutor"),
                "EmailAddress": kw.get("addr", "tutor@manchester.ac.uk"),
                "RoutingType": "SMTP",
            }
        },
        "Preview": kw.get("preview", "Hello   there"),
        "IsRead": kw.get("read", False),
        "Flag": {"FlagStatus": "Flagged" if kw.get("flagged") else "NotFlagged"},
        "Importance": "High" if kw.get("high") else "Normal",
        "HasAttachments": True,
    }


def find_response(items: list[dict]) -> dict:
    return {
        "Body": {
            "ResponseMessages": {
                "Items": [
                    {
                        "__type": "FindItemResponseMessage:#Exchange",
                        "ResponseClass": "Success",
                        "RootFolder": {"Items": items, "TotalItemsInView": len(items)},
                    }
                ]
            }
        }
    }


def get_response(bodies: dict[str, str], kind: str = "Text") -> dict:
    return {
        "Body": {
            "ResponseMessages": {
                "Items": [
                    {
                        "Items": [
                            {
                                "__type": "Message:#Exchange",
                                "ItemId": {"Id": i},
                                "Body": {"BodyType": kind, "Value": text},
                            }
                            for i, text in bodies.items()
                        ]
                    }
                ]
            }
        }
    }
