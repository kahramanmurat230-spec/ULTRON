"""Live GUI Agent integration with approval-gated screen actions."""
import re

from app.agent.gui_agent import GUIAction, GUIAgent
from app.automation.gui import GUIAutomation


def try_gui_handle(agent, text, approved=False):
    t = (text or "").strip().lower()
    if not any(k in t for k in ("ekranda", "ekranımda", "ekranimi", "butona", "butonuna")):
        return None
    if not any(k in t for k in ("tıkla", "tikla", "yaz", "bas")):
        return None
    match = re.search(r"['\"]([^'\"]+)['\"]", text or "")
    if not match or not any(k in t for k in ("tıkla", "tikla")):
        return None
    automation = getattr(agent, "gui", None) or GUIAutomation()
    result = GUIAgent(automation).execute(
        [GUIAction("click_text", params={"text": match.group(1)})], approved=approved
    )
    if result.get("status") == "WAITING_APPROVAL":
        return "Onay gerekiyor: ekrandaki hedefe tıklama işlemi bekliyor."
    if not result.get("ok"):
        error = result.get("results", [{}])[-1].get("error", "bilinmeyen hata")
        return f"GUI işlemi başarısız: {error}"
    return f"Ekrandaki '{match.group(1)}' hedefi işlendi ve doğrulama uygulandı."


def _install():
    from app.agent.agent import Agent
    if getattr(Agent, "_gui_agent_connected", False):
        return
    original = Agent.handle

    def wrapped(self, text, approved=False):
        try:
            answer = try_gui_handle(self, text, approved=approved)
        except Exception as exc:
            try:
                self.audit.write("GUI_AGENT", f"status=ERROR error={exc}")
            except Exception:
                pass
            answer = None
        return answer if answer is not None else original(self, text, approved=approved)

    Agent.handle = wrapped
    Agent._gui_agent_connected = True


_install()
