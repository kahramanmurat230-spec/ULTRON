import { useEffect, useRef, useState } from "react";
import { executeCommand } from "../lib/api";
import { useApp } from "../lib/store";

/**
 * TerminalChat — sol panel: minimal terminal/sohbet geçmişi.
 *
 * Üstte ULTRON logosu + "YOUR PERSONAL AI ASSISTANT" başlığı, altında
 * monospace kırmızı temalı konuşma geçmişi (store.chat), en altta canlı
 * terminal cursor'ı ve komut girişi. Komutlar mevcut /api/agent/command
 * akışına gider (executeCommand), backend mantığına dokunulmaz.
 */
export function TerminalChat() {
  const chat = useApp((s) => s.chat);
  const agentState = useApp((s) => s.agentState);
  const connected = useApp((s) => s.connected);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // yeni mesajda en alta kaydır
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat, agentState]);

  const submit = async () => {
    const t = text.trim();
    if (!t) return;
    setErr(null);
    setText("");
    try {
      const r = await executeCommand(t);
      if (!r.ok) setErr(r.error ?? "komut reddedildi");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "backend'e ulaşılamadı");
    }
  };

  const thinking = ["THINKING", "PLANNING", "EXECUTING", "VERIFYING"].includes(agentState);

  return (
    <aside className="term">
      {/* --- marka --- */}
      <header className="term-brand">
        <svg className="term-logo" viewBox="0 0 120 108" width="30" height="27" aria-hidden>
          <path d="M60 6 L112 100 L8 100 Z" fill="#ff1a1a" />
          <path d="M60 30 L92 92 L58 92 L74 62 Z" fill="#000" />
          <path d="M60 30 L28 92 L50 92 L60 54 Z" fill="#000" opacity="0.55" />
        </svg>
        <div className="term-brand-txt">
          <div className="term-title">ULTRON</div>
          <div className="term-sub">YOUR PERSONAL AI ASSISTANT</div>
        </div>
      </header>

      {/* --- konuşma geçmişi --- */}
      <div className="term-log" ref={scrollRef}>
        {chat.length === 0 && (
          <div className="term-empty">
            <div>ULTRON: Sistem çevrimiçi. Emirlerinizi bekliyorum, Boss.</div>
          </div>
        )}
        {chat.map((line, i) =>
          line.role === "user" ? (
            <div className="term-line" key={i}>
              <span className="term-you">&gt; Sen:</span>{" "}
              <span className="term-you-txt">{line.text}</span>
            </div>
          ) : (
            <div className="term-line" key={i}>
              <span className="term-ai">ULTRON:</span>{" "}
              <span className="term-ai-txt">{line.text}</span>
            </div>
          )
        )}
        {thinking && (
          <div className="term-line term-thinking">
            <span className="term-ai">ULTRON:</span>{" "}
            <span className="term-ai-txt">işleniyor<span className="term-dots" /></span>
          </div>
        )}
      </div>

      {/* --- komut satırı / cursor --- */}
      <div className="term-prompt">
        <span className="term-caret">&gt;</span>
        <input
          className="term-input"
          placeholder=""
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
          }}
          spellCheck={false}
          autoComplete="off"
        />
        {text.length === 0 && <span className="term-blink" />}
      </div>
      {err && <div className="term-err">⚠ {err}</div>}
      <div className="term-status">
        <span className={"term-dot " + (connected ? "on" : "off")} />
        {connected ? "LINK ACTIVE" : "RECONNECTING…"}
      </div>
    </aside>
  );
}
