"""PHASE 11: email foundation — REAL protocol-level tests.

Local SMTP and IMAP servers implemented over real TCP sockets; the
connector speaks actual SMTP/IMAP4rev1 to them (smtplib/imaplib clients).
No mocks of connector behavior — only a real local wire.
"""
import email.policy
import os
import socket
import socketserver
import sys
import threading
import time
from email.message import EmailMessage

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.connectors.email import EmailConnector  # noqa: E402
from app.connectors import ConnectorError  # noqa: E402


# ------------------------------------------------------------ SMTP server
class SMTPHandler(socketserver.StreamRequestHandler):
    received = []  # class-level: son DATA içeriği

    def _send(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self._send("220 local.smtp ULTRON-test ready")
        data_mode = False
        buf = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip("\r\n")
            if data_mode:
                if text == ".":
                    SMTPHandler.received.append("\r\n".join(buf))
                    self._send("250 OK message accepted")
                    data_mode, buf = False, []
                else:
                    buf.append(text[1:] if text.startswith("..") else text)
                continue
            cmd = text.split(" ")[0].upper()
            if cmd in ("HELO", "EHLO"):
                if cmd == "EHLO":
                    self._send("250-local.smtp")
                    self._send("250-AUTH LOGIN")
                    self._send("250 OK")
                else:
                    self._send("250 local.smtp")
            elif cmd == "STARTTLS":
                self._send("454 TLS not available")  # test: STARTTLS yok → düz devam
            elif cmd == "AUTH":
                self._send("235 authenticated")
            elif cmd == "LOGIN":
                self._send("235 authenticated")
            elif cmd == "MAIL":
                self._send("250 OK")
            elif cmd == "RCPT":
                self._send("250 OK")
            elif cmd == "DATA":
                self._send("354 end with <CRLF>.<CRLF>")
                data_mode = True
            elif cmd == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("250 OK")


class IMAPHandler(socketserver.StreamRequestHandler):
    mailbox = []  # class-level: raw mesajlar

    def _send(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self._send("* OK IMAP4rev1 ULTRON-test ready")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip("\r\n")
            parts = text.split(" ")
            if len(parts) < 2:
                self._send("* BAD")
                continue
            tag, cmd = parts[0], parts[1].upper()
            if cmd == "CAPABILITY":
                self._send("* CAPABILITY IMAP4rev1 AUTH=LOGIN")
                self._send(f"{tag} OK CAPABILITY completed")
            elif cmd == "LOGIN":
                self._send(f"{tag} OK LOGIN completed")
            elif cmd == "SELECT":
                self._send(f"* {len(IMAPHandler.mailbox)} EXISTS")
                self._send("* 0 RECENT")
                self._send("* OK [UIDVALIDITY 1] UIDs valid")
                self._send(f"{tag} OK [READ-WRITE] SELECT completed")
            elif cmd == "SEARCH":
                # '(SUBJECT "x")' → subject filtresi; ALL → hepsi
                if "SUBJECT" in text.upper():
                    kw = text.split('"')[1] if '"' in text else ""
                    hits = [str(i + 1) for i, raw in enumerate(IMAPHandler.mailbox)
                            if kw.lower() in raw.decode(errors="replace").lower()]
                else:
                    hits = [str(i + 1) for i in range(len(IMAPHandler.mailbox))]
                self._send("* SEARCH " + " ".join(hits) if hits else "* SEARCH")
                self._send(f"{tag} OK SEARCH completed")
            elif cmd == "FETCH":
                idx = int(parts[2]) - 1
                raw = IMAPHandler.mailbox[idx]
                self._send(f"* {parts[2]} FETCH (RFC822 {{{len(raw)}}}")
                self.wfile.write(raw)
                self.wfile.write(b")\r\n")
                self._send(f"{tag} OK FETCH completed")
            elif cmd == "LOGOUT":
                self._send("* BYE")
                self._send(f"{tag} OK LOGOUT completed")
                return
            else:
                self._send(f"{tag} OK {cmd} completed")


def start_server(handler, port=0):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler, bind_and_activate=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


class FakeVault:
    def __init__(self, addr=None, pwd=None):
        self.d = {"email_address": addr, "email_password": pwd}

    def get(self, k):
        return self.d.get(k)


def make_email(smtp_port, imap_port, addr="boss@ultron.test", pwd="s3cret"):
    return EmailConnector(
        vault=FakeVault(addr, pwd),
        settings={"email": {"smtp_host": "127.0.0.1", "smtp_port": smtp_port,
                            "imap_host": "127.0.0.1", "imap_port": imap_port,
                            "timeout_s": 5}},
        drafts_dir="/tmp/ultron_email_test/drafts",
        attachments_dir="/tmp/ultron_email_test/att")


@pytest.fixture(scope="module")
def servers():
    SMTPHandler.received = []
    msg = EmailMessage()
    msg["From"] = "alice@x.test"
    msg["To"] = "boss@ultron.test"
    msg["Subject"] = "UltrOn Deneme Maili"
    msg["Date"] = "Fri, 28 Aug 2026 10:00:00 +0000"
    msg.set_content("Merhaba Boss, bu bir test mesajıdır.")
    msg.add_attachment(b"PDFDATA-123", maintype="application",
                       subtype="pdf", filename="rapor.pdf")
    IMAPHandler.mailbox = [msg.as_bytes()]
    s1, p1 = start_server(SMTPHandler)
    s2, p2 = start_server(IMAPHandler)
    yield p1, p2
    s1.shutdown(); s2.shutdown()


# ---------------------------------------------------------------- send
def test_send_real_smtp_wire(servers, tmp_path):
    smtp_port, _ = servers
    ec = make_email(smtp_port, 0)
    att = tmp_path / "ek.txt"
    att.write_text("ek içeriği", encoding="utf-8")
    res = ec.send("hedef@ornek.test", "Test Konu", "Gövde metni", attachments=[str(att)])
    assert res["ok"] is True and res["attachments"] == 1
    raw = SMTPHandler.received[-1]
    assert "From: boss@ultron.test" in raw and "Subject: Test Konu" in raw
    assert "hedef@ornek.test" in raw


def test_send_invalid_recipient_rejected(servers):
    smtp_port, _ = servers
    ec = make_email(smtp_port, 0)
    with pytest.raises(ConnectorError):
        ec.send("gecersiz-adres", "k", "b")


def test_send_without_credentials_honest():
    ec = EmailConnector(vault=FakeVault(None, None),
                        settings={"email": {"smtp_host": "127.0.0.1"}})
    with pytest.raises(ConnectorError) as ei:
        ec.send("a@b.c", "k", "b")
    assert "vault" in str(ei.value)


def test_draft_lifecycle(servers):
    smtp_port, _ = servers
    ec = make_email(smtp_port, 0)
    d = ec.draft("taslak@x.test", " taslak konu ", "gövde")
    assert d["ok"] and os.path.exists(d["draft"])
    all_drafts = ec.drafts()
    assert any(x["to"] == "taslak@x.test" for x in all_drafts)


# ---------------------------------------------------------------- imap
def test_inbox_read_real_imap_wire(servers):
    _, imap_port = servers
    ec = make_email(0, imap_port)
    res = ec.inbox_read(limit=5)
    assert res["ok"] and res["count"] == 1
    m = res["messages"][0]
    assert m["from"] == "alice@x.test"
    assert "Deneme Maili" in m["subject"]
    assert "test mesajı" in m["body"]
    assert m["attachments"] == ["rapor.pdf"]


def test_search_real_imap_wire(servers):
    _, imap_port = servers
    ec = make_email(0, imap_port)
    hit = ec.search("Deneme")
    assert hit["ok"] and hit["count"] == 1
    miss = ec.search("BoyleBirKonuYok")
    assert miss["count"] == 0


def test_parse_attachment_save(servers):
    _, imap_port = servers
    ec = make_email(0, imap_port)
    res = ec.inbox_read()
    path = ec.save_attachment(b"XYZ", "e k@li.svg.png")
    assert os.path.exists(path) and "@" not in os.path.basename(path)  # sanitize


def test_email_risk_and_approval_registration():
    from app.security.risk import evaluate
    d = evaluate("email_send", {"to": "a@b.c", "subject": "x", "body": "y"},
                 dangerous=True)
    assert d["level"] == "HIGH" and d["requires_approval"] is True
