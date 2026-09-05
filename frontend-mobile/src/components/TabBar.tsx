import type { Page } from "../App";

const I = {
  home: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" /></svg>,
  chat: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M4 6h16v10H8l-4 4z" /></svg>,
  voice: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="9" y="3" width="6" height="10" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>,
  vision: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6-10-6-10-6z" /><circle cx="12" cy="12" r="2.5" /></svg>,
  more: <svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></svg>,
};

export function TabBar({ page, go }: { page: Page; go: (p: Page) => void }) {
  const tabs: { id: Page; label: string }[] = [
    { id: "home", label: "CORE" },
    { id: "chat", label: "CHAT" },
    { id: "voice", label: "VOICE" },
    { id: "vision", label: "VISION" },
    { id: "more", label: "MORE" },
  ];
  return (
    <nav className="tabbar">
      {tabs.map((t) => (
        <button key={t.id} className={"tab" + (page === t.id ? " on" : "")} onClick={() => go(t.id)}>
          {I[t.id]}
          {t.label}
        </button>
      ))}
    </nav>
  );
}
