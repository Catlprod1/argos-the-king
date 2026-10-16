"""Read-only Gmail inbox metadata summary; no bodies, attachments, or send API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class GmailMetadataReader:
    def __init__(self, opener=urllib.request.urlopen):
        self.opener = opener

    def _json(self, request: urllib.request.Request) -> dict:
        try:
            with self.opener(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"Gmail returned HTTP {exc.code}; check read-only OAuth access") from None
        except (OSError, ValueError, TypeError):
            raise ValueError("Gmail could not be reached or returned invalid data") from None

    def summary(self, query: str = "") -> str:
        if len(query) > 120 or "\n" in query or "\r" in query:
            return "Email search is limited to one line and 120 characters."
        client = os.environ.get("ARGOS_GMAIL_CLIENT_ID", "")
        secret = os.environ.get("ARGOS_GMAIL_CLIENT_SECRET", "")
        refresh = os.environ.get("ARGOS_GMAIL_REFRESH_TOKEN", "")
        if not all((client, secret, refresh)):
            return "Email is not connected. Gmail read-only OAuth authorization is required."
        form = urllib.parse.urlencode({"client_id": client, "client_secret": secret,
                                       "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
        auth = self._json(urllib.request.Request(
            "https://oauth2.googleapis.com/token", form,
            {"Content-Type": "application/x-www-form-urlencoded"}, method="POST"))
        token = auth.get("access_token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise ValueError("Gmail OAuth refresh did not provide access")
        base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

        def get(url: str) -> dict:
            return self._json(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}))

        params = {"maxResults": 5, "labelIds": "INBOX"}
        if query.strip():
            params["q"] = query.strip()
        listing = get(base + "?" + urllib.parse.urlencode(params))
        messages = listing.get("messages", []) if isinstance(listing, dict) else []
        if not isinstance(messages, list):
            raise ValueError("Gmail returned invalid message listing")
        lines = ["Inbox search (metadata only; no email contents read):" if query.strip()
                 else "Recent inbox subjects (metadata only; no email contents read):"]
        for item in messages[:5]:
            identifier = item.get("id") if isinstance(item, dict) else None
            if not isinstance(identifier, str) or not identifier.isalnum():
                continue
            query = urllib.parse.urlencode({"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]}, doseq=True)
            msg = get(base + "/" + identifier + "?" + query)
            headers = msg.get("payload", {}).get("headers", [])
            fields = {h.get("name", "").lower(): h.get("value", "") for h in headers if isinstance(h, dict)}
            subject = fields.get("subject", "(no subject)").replace("\n", " ").replace("\r", " ")[:120]
            sender = fields.get("from", "unknown sender").replace("\n", " ").replace("\r", " ")[:100]
            unread = "unread" if "UNREAD" in msg.get("labelIds", []) else "read"
            lines.append(f"- [{unread}] {subject} — {sender}")
        if len(lines) == 1:
            lines.append("No recent inbox messages found.")
        lines.append("Subject lines can be misleading. Check the original email before acting.")
        return "\n".join(lines)
