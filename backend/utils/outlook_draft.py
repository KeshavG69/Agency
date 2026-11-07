"""Create a new DRAFT, with attachments, in the user's OWN Outlook mailbox.

Draft only: it sits in their Drafts folder for THEM to read and send, the same discipline as
create_outlook_draft_reply. No send call exists on this path.

Composio's Outlook actions don't cover attachments of any real size, so this goes to Microsoft
Graph through the same proxy the SharePoint code uses. Graph takes attachments under 3 MB inline;
anything bigger needs an upload session, whose URL is pre-authenticated and called directly.
"""
from __future__ import annotations

import base64
import logging

import httpx

from utils.composio_utils import connection_status
from utils.sharepoint_writer import _graph_request

logger = logging.getLogger(__name__)

_INLINE_LIMIT = 3 * 1024 * 1024
_CHUNK = 320 * 1024 * 10  # Graph requires multiples of 320 KB


class OutlookNotConnected(Exception):
    pass


def _upload_large(account: str, message_id: str, name: str, content: bytes) -> None:
    _, session = _graph_request(
        "POST", f"me/messages/{message_id}/attachments/createUploadSession", account,
        body={"AttachmentItem": {"attachmentType": "file", "name": name, "size": len(content)}},
    )
    url = (session or {}).get("uploadUrl")
    if not url:
        raise RuntimeError(f"Outlook wouldn't start an upload for {name}")
    with httpx.Client(timeout=120) as client:
        for start in range(0, len(content), _CHUNK):
            chunk = content[start:start + _CHUNK]
            end = start + len(chunk) - 1
            r = client.put(url, content=chunk, headers={
                "Content-Range": f"bytes {start}-{end}/{len(content)}",
                "Content-Length": str(len(chunk)),
            })
            r.raise_for_status()


def create_draft(user_email: str, to: list[str], subject: str, body: str,
                 attachments: list[tuple[str, bytes]]) -> dict:
    """Returns {"id", "web_link"}. Raises OutlookNotConnected if this user hasn't connected.

    The last gate before anything leaves: the subject and body lose hidden characters, and every
    attachment is cleaned (utils/scrub.py) — including files a person uploaded, whose own
    properties are kept."""
    from utils.scrub import clean_file, clean_text

    status = connection_status("outlook", user_email)
    account = status.get("connected_account_id") if status.get("connected") else None
    if not account:
        raise OutlookNotConnected(user_email)

    attachments = [(name, clean_file(name, content)) for name, content in attachments]
    _, msg = _graph_request("POST", "me/messages", account, body={
        "subject": clean_text(subject),
        "body": {"contentType": "Text", "content": clean_text(body)},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
    })
    message_id = (msg or {}).get("id")
    if not message_id:
        raise RuntimeError("Outlook didn't return a draft")
    for name, content in attachments:
        if len(content) < _INLINE_LIMIT:
            _graph_request("POST", f"me/messages/{message_id}/attachments", account, body={
                "@odata.type": "#microsoft.graph.fileAttachment", "name": name,
                "contentBytes": base64.b64encode(content).decode(),
            })
        else:
            _upload_large(account, message_id, name, content)
    return {"id": message_id, "web_link": msg.get("webLink")}
