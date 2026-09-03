import { useEffect, useState } from "react";

import { getAuthProviders, login, type AuthProviders } from "./api";
import { personName } from "./labels";
import type { OrganizationProfile } from "./viewModels";

export function LoginPage({ onLoggedIn }: { onLoggedIn: (profile: OrganizationProfile) => void }) {
  const [providers, setProviders] = useState<AuthProviders | null>(null);
  const [account, setAccount] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getAuthProviders()
      .then((next) => {
        if (cancelled) return;
        setProviders(next);
        setAccount(next.accounts[0]?.id ?? "");
      })
      .catch(() => {
        if (!cancelled) setError("로그인 방법을 불러오지 못했습니다. 서버 연결을 확인해 주세요.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit() {
    if (!account) return;
    setIsWorking(true);
    setError(null);
    try {
      onLoggedIn(await login(account));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-brand" aria-hidden>
        <div className="wordmark">
          <span className="wordmark-mark">SC</span>
          SCAX
        </div>
        <h1>기록 → 판단 → 수행 → 보고를 한 흐름으로</h1>
        <p>조직의 업무를 하나의 원장에서 다루고, AX가 허용된 범위 안에서 조회하고 제안합니다.</p>
      </section>
      <section aria-label="로그인" className="login-panel">
        <h2>로그인</h2>
        <p className="login-lead">SCAX 계정으로 로그인합니다.</p>

        <button className="btn h40 login-oidc" disabled title="Google 로그인은 조직 SSO 연결 뒤에 열립니다" type="button">
          <span aria-hidden className="google-mark">G</span> Google 계정으로 로그인
        </button>
        <p className="login-hint">Google 로그인은 조직 SSO 연결 뒤에 열립니다.</p>

        {providers?.developer && (
          <form
            className="login-dev"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <div className="login-divider">
              <span>로컬 실행 전용</span>
            </div>
            <fieldset className="account-list">
              <legend>누구로 로그인할까요?</legend>
              {providers.accounts
                .filter((item) => item.id !== "demo-admin")
                .map((item) => (
                  <label className={account === item.id ? "account-option selected" : "account-option"} key={item.id}>
                    <input checked={account === item.id} name="account" onChange={() => setAccount(item.id)} type="radio" value={item.id} />
                    <span className="avatar md" aria-hidden>
                      {personName(item.display_name).slice(0, 1)}
                    </span>
                    <span className="account-name">{item.display_name}</span>
                  </label>
                ))}
            </fieldset>
            <button className="btn h40 primary" disabled={isWorking || !account} type="submit">
              {isWorking ? "로그인 중…" : "로그인"}
            </button>
          </form>
        )}
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
      </section>
    </main>
  );
}
