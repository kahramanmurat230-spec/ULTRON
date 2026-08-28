import { useEffect, useState } from "react";
import { connectWS, handshake } from "./lib/api";
import { startMeshWatch } from "./lib/mesh_client";
import { useMeshTheme } from "./lib/useMeshTheme";
import { useM } from "./lib/store";
import { TabBar } from "./components/TabBar";
import { Home } from "./pages/Home";
import { Chat } from "./pages/Chat";
import { Voice } from "./pages/Voice";
import { Vision } from "./pages/Vision";
import { More } from "./pages/More";

export type Page = "home" | "chat" | "voice" | "vision" | "more";

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const connected = useM((s) => s.connected);
  const agentState = useM((s) => s.agentState);
  useMeshTheme(); // PC tema senkronu (offline → local)

  useEffect(() => {
    void handshake().finally(() => { connectWS(); startMeshWatch(); });
  }, []);

  return (
    <div className="app">
      <div className="top">
        <span className="logo"><b>ULTRON</b> MOBILE</span>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="state-chip">{agentState}</span>
          <span className={"badge " + (connected ? "g" : "r")}>{connected ? "ONLINE" : "OFFLINE"}</span>
        </span>
      </div>
      <div className="page">
        {page === "home" && <Home go={setPage} />}
        {page === "chat" && <Chat />}
        {page === "voice" && <Voice />}
        {page === "vision" && <Vision />}
        {page === "more" && <More />}
      </div>
      <TabBar page={page} go={setPage} />
    </div>
  );
}
