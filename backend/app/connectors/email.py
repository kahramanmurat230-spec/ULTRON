"""Email foundation — real SMTP/IMAP over stdlib, vault-managed credentials.

- Credentials NEVER in code/settings: vault secrets `email_address` +
  `email_password` (or `email_oauth_token`); without them every call
  raises a precise error — no fake mailboxes.
- send(): SMTP_SSL or STARTTLS; registered as the dangerous tool
  `email_send` (HIGH risk) — approval-gated.
- inbox_read()/search(): IMAP4rev1 LOGIN/SELECT/SEARCH/FETCH with
  email.message_from_bytes parsing (headers, body, attachment parts).
- draft(): stores JSON drafts locally (data/email/drafts) — sendless.
- attachments: saved only into the sandbox-checked downloads dir.
- All network ops carry hard timeouts; nothing is logged with the
  password (audit receives addresses/subjects only).
"""
import email
import email.policy
import imaplib
import json
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

from app.connectors import ConnectorError

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")


class EmailConnector:
    NAME = "email"

    def __init__(self, vault=None, settings=None, drafts_dir="data/email/drafts",
                 attachments_dir="data/email/attachments"):
        cfg = (settings or {}).get("email", {})
        self.vault = vault
        self.smtp_host = cfg.get("smtp_host")
        self.smtp_port = int(cfg.get("smtp_port", 587))
        self.smtp_ssl = bool(cfg.get("smtp_ssl", False))
        self.imap_host = cfg.get("imap_host")
        self.imap_port = int(cfg.get("imap_port", 993))
        self.timeout = float(cfg.get("timeout_s", 15))
        self.drafts_dir = Path(drafts_dir)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir = Path(attachments_dir)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ creds
    def _creds(self):
        addr = pwd = token = None
        if self.vault is not None:
            try:
                addr = self.vault.get("email_address")
                pwd = self.vault.get("email_password")
                token = self.vault.get("email_oauth_token")
            except Exception:
                pass
        if not addr:
            raise ConnectorError("email kimliği yok — vault'a 'email_address' ve "
                                 "'email_password' (veya 'email_oauth_token') girin")
        return addr, pwd or token

    def _hosts_for(self, addr):
        domain = addr.split("@")[-1]
        smtp = self.smtp_host or f"smtp.{domain}"
        imap = self.imap_host or f"imap.{domain}"
        return smtp, imap

    def health(self) -> dict:
        try:
            addr, _ = self._creds()
            return {"connector": self.NAME, "configured": True, "address": addr}
        except ConnectorError as exc:
            return {"connector": self.NAME, "configured": False, "error": str(exc)}

    # ------------------------------------------------------------ send
    @staticmethod
    def _valid_addr(a: str) -> bool:
        return bool(EMAIL_RE.match(a or ""))

    def send(self, to: str, subject: str, body: str,
             attachments: list | None = None, cc: str | None = None) -> dict:
        addr, secret = self._creds()
        if not self._valid_addr(to):
            raise ConnectorError(f"geçersiz alıcı: {to!r}")
        if cc and not self._valid_addr(cc):
            raise ConnectorError(f"geçersiz cc: {cc!r}")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = addr, to, str(subject)[:200]
        if cc:
            msg["Cc"] = cc
        msg.set_content(str(body)[:100_000])
        for path in (attachments or []):
            p = Path(path)
            if not p.exists():
                raise ConnectorError(f"ek dosyası yok: {path}")
            if p.stat().st_size > 10 * 1024 * 1024:
                raise ConnectorError("ek 10MB sınırını aşıyor")
            msg.add_attachment(p.read_bytes(), maintype="application",
                               subtype="octet-stream", filename=p.name)
        smtp_host, _ = self._hosts_for(addr)
        t0 = time.time()
        try:
            if self.smtp_ssl:
                server = smtplib.SMTP_SSL(smtp_host, self.smtp_port,
                                          timeout=self.timeout,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(smtp_host, self.smtp_port, timeout=self.timeout)
                try:
                    server.starttls(context=ssl.create_default_context())
                except smtplib.SMTPNotSupportedError:
                    pass  # yerel test sunucusu: TLS yok, dürüst devam
            with server:
                server.login(addr, secret)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise ConnectorError(f"SMTP kimlik hatası: {str(exc)[:120]}") from exc
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"SMTP hatası ({smtp_host}:{self.smtp_port}): "
                                 f"{str(exc)[:140]}") from exc
        return {"ok": True, "to": to, "subject": str(subject)[:80],
                "attachments": len(attachments or []),
                "elapsed_s": round(time.time() - t0, 2)}

    # ------------------------------------------------------------ draft
    def draft(self, to: str, subject: str, body: str) -> dict:
        if not self._valid_addr(to):
            raise ConnectorError(f"geçersiz alıcı: {to!r}")
        d = {"to": to, "subject": str(subject)[:200], "body": str(body)[:100_000],
             "created": time.time()}
        path = self.drafts_dir / f"draft_{int(time.time() * 1000)}.json"
        path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "draft": str(path), "to": to}

    def drafts(self) -> list[dict]:
        out = []
        for f in sorted(self.drafts_dir.glob("draft_*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    # ------------------------------------------------------------ imap
    def _imap_connect(self):
        addr, secret = self._creds()
        _, imap_host = self._hosts_for(addr)
        try:
            m = imaplib.IMAP4_SSL(imap_host, self.imap_port, timeout=self.timeout) \
                if self.imap_port == 993 and not self.imap_host else \
                imaplib.IMAP4(imap_host, self.imap_port, timeout=self.timeout)
            m.login(addr, secret)
            return m
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"IMAP hatası ({imap_host}:{self.imap_port}): "
                                 f"{str(exc)[:140]}") from exc

    @staticmethod
    def _parse_message(raw: bytes) -> dict:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        out = {"from": str(msg.get("From", "")), "to": str(msg.get("To", "")),
               "subject": str(msg.get("Subject", "")),
               "date": str(msg.get("Date", "")), "attachments": [],
               "body": ""}
        body = msg.get_body(preferencelist=("plain",))
        if body is not None:
            try:
                out["body"] = body.get_content()[:20_000]
            except Exception:
                out["body"] = ""
        for part in msg.iter_attachments():
            out["attachments"].append(part.get_filename() or "(isimsiz)")
        return out

    def inbox_read(self, limit: int = 10, mailbox: str = "INBOX") -> dict:
        m = self._imap_connect()
        try:
            m.select(mailbox)
            typ, data = m.search(None, "ALL")
            if typ != "OK":
                raise ConnectorError(f"IMAP SEARCH başarısız: {typ}")
            ids = data[0].split()[-max(1, min(int(limit), 50)):]
            msgs = []
            for i in reversed(ids):  # en yeni önce
                typ, fd = m.fetch(i, "(RFC822)")
                if typ != "OK" or not fd or fd[0] is None:
                    continue
                raw = fd[0][1]
                parsed = self._parse_message(raw)
                parsed["uid"] = i.decode()
                msgs.append(parsed)
            return {"ok": True, "count": len(msgs), "messages": msgs}
        finally:
            try:
                m.logout()
            except Exception:
                pass

    def search(self, query: str, mailbox: str = "INBOX", limit: int = 10) -> dict:
        if not query or any(ch in query for ch in '"()\\'):
            raise ConnectorError("geçersiz arama sorgusu")
        try:
            query.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ConnectorError("IMAP arama sorgusu ASCII olmalı (protokol sınırı)") from exc
        m = self._imap_connect()
        try:
            m.select(mailbox)
            typ, data = m.search(None, f'(SUBJECT "{query}")')
            if typ != "OK":
                raise ConnectorError(f"IMAP SEARCH başarısız: {typ}")
            ids = data[0].split()[: max(1, min(int(limit), 50))]
            return {"ok": True, "query": query, "uids": [i.decode() for i in ids],
                    "count": len(ids)}
        finally:
            try:
                m.logout()
            except Exception:
                pass

    def save_attachment(self, raw: bytes, filename: str) -> str:
        """Gövdeye gömülü eki diske yazar (sandbox kökleri altına)."""
        safe = re.sub(r"[^\w.\-]", "_", filename or "attachment")[:120]
        p = self.attachments_dir / safe
        p.write_bytes(raw)
        return str(p)
