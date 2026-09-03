import { useEffect, useState } from "react";

import { getDeveloperPersonas } from "./api";
import { ActionInboxPage } from "./ActionInboxPage";
import { DailyReportPage } from "./DailyReportPage";
import { MyWorkPage } from "./MyWorkPage";
import { OrgPage } from "./OrgPage";
import { TodayPage } from "./TodayPage";
import type { Persona, ProductSurface } from "./viewModels";

import "./task.css";

const navigation: ReadonlyArray<{ id: ProductSurface; label: string }> = [
  { id: "today", label: "오늘" },
  { id: "work", label: "내 업무" },
  { id: "inbox", label: "판단" },
  { id: "report", label: "보고" },
  { id: "org", label: "조직" },
];

export default function App() {
  const [personaId, setPersonaId] = useState("mina");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [surface, setSurface] = useState<ProductSurface>("today");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void getDeveloperPersonas()
      .then((availablePersonas) => {
        if (cancelled) return;
        setPersonas(availablePersonas.filter((persona) => persona.id !== "demo-admin"));
      })
      .catch(() => {
        if (!cancelled) setError("사용자 정보를 불러오지 못했습니다.");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const currentPersona = personas.find((persona) => persona.id === personaId);
  const currentPersonaName = currentPersona?.display_name ?? "사용자";
  const pageProps = { personaId, onError: setError };

  return (
    <main className="thesc-shell">
      <aside className="rail">
        <div className="wordmark">
          <span className="wordmark-mark" />
          SCAX AX
        </div>
        <div className="profile">
          <span className="avatar">{currentPersonaName.slice(0, 1)}</span>
          <div>
            <b>{currentPersonaName}</b>
            <small>워크스페이스</small>
          </div>
        </div>
        <nav aria-label="제품 탐색">
          {navigation.map((item) => (
            <button
              className={surface === item.id ? "active" : ""}
              key={item.id}
              onClick={() => setSurface(item.id)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <section className="canvas">
        <header className="canvas-topbar">
          <span className="date-chip">2026년 9월 3일</span>
          <label className="persona-picker">
            사용자
            <select onChange={(event) => setPersonaId(event.target.value)} value={personaId}>
              {personas.map((persona) => (
                <option key={persona.id} value={persona.id}>
                  {persona.display_name}
                </option>
              ))}
            </select>
          </label>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            {error}
            <button onClick={() => setError(null)} type="button">
              닫기
            </button>
          </div>
        )}

        {surface === "today" && <TodayPage {...pageProps} onNavigate={setSurface} />}
        {surface === "work" && <MyWorkPage {...pageProps} />}
        {surface === "inbox" && <ActionInboxPage {...pageProps} />}
        {surface === "report" && <DailyReportPage {...pageProps} />}
        {surface === "org" && <OrgPage {...pageProps} />}
      </section>
    </main>
  );
}
