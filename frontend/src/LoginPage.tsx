import { useEffect, useState } from "react";

import { getAuthProviders, login, type AuthProviders } from "./api";
import { personName } from "./labels";
import { Icon } from "./Icon";
import type { OrganizationProfile } from "./viewModels";

/**
 * Signing in is proving who you are, and nothing more.
 *
 * An address and a password go to the server, and what that person may then do is read from the Organization & Access
 * ledger, not from this form. On a developer machine the server also hands over the demo's own accounts, and pressing
 * one fills this same form and submits it — a way to skip typing, not a way to skip signing in. Nothing here can make
 * someone else's session: becoming another person still means signing out and signing in as them.
 */
export function LoginPage({ onLoggedIn }: { onLoggedIn: (profile: OrganizationProfile) => void }) {
  const [providers, setProviders] = useState<AuthProviders | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getAuthProviders()
      .then((next) => {
        if (!cancelled) setProviders(next);
      })
      .catch(() => {
        if (!cancelled) setError("로그인 방법을 불러오지 못했습니다. 서버 연결을 확인해 주세요.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit(credentials?: { email: string; password: string }) {
    const address = (credentials?.email ?? email).trim();
    const secret = credentials?.password ?? password;
    if (!address || !secret) return;
    setIsWorking(true);
    setError(null);
    try {
      onLoggedIn(await login(address, secret));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  /** Fill the form with a demo account and sign in with it, so what is being sent stays visible. */
  async function signInAs(account: { email: string }) {
    const secret = providers?.demo_password ?? "";
    setEmail(account.email);
    setPassword(secret);
    await submit({ email: account.email, password: secret });
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
        <header className="login-heading">
          <h2>로그인</h2>
          <p className="login-lead">SCAX 계정으로 업무를 시작하세요.</p>
        </header>

        {(providers?.demo_accounts?.length ?? 0) > 0 && (
          <section aria-label="로컬 데모 계정" className="login-demo">
            <div className="login-demo-heading">
              <h3>바로 로그인</h3>
              <span className="login-local-badge">로컬 전용</span>
            </div>
            <ul className="demo-account-list">
              {(providers?.demo_accounts ?? []).map((account) => (
                <li key={account.member_id}>
                  <button
                    className="demo-account"
                    data-demo-account={account.member_id}
                    disabled={isWorking}
                    onClick={() => void signInAs(account)}
                    type="button"
                  >
                    <span aria-hidden className="avatar login-account-avatar">
                      {personName(account.display_name).slice(0, 1)}
                    </span>
                    <span className="demo-account-main">
                      <b>{account.display_name}</b>
                      <small className="t-meta">{account.email}</small>
                    </span>
                    <Icon name="chevron-right" className="demo-account-arrow" />
                  </button>
                </li>
              ))}
            </ul>
            <p className="login-hint">
              계정을 선택하면 바로 로그인됩니다.
            </p>
          </section>
        )}

        {providers?.local && (
          <form
            className="login-local"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <div className="login-divider">
              <span>이메일로 로그인</span>
            </div>
            <label className="field" htmlFor="login-email">
              <span>이메일</span>
              <input
                autoComplete="username"
                id="login-email"
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                value={email}
              />
            </label>
            <label className="field" htmlFor="login-password">
              <span>비밀번호</span>
              <input
                autoComplete="current-password"
                id="login-password"
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                value={password}
              />
            </label>
            <button className="btn h40 primary" disabled={isWorking || !email.trim() || !password} type="submit">
              {isWorking ? "로그인 중…" : "로그인"}
            </button>
          </form>
        )}
        <div className="login-sso">
          <button className="btn h40 login-oidc" disabled title="Google 로그인은 조직 SSO 연결 뒤에 열립니다" type="button">
            <span aria-hidden className="google-mark">G</span> Google 계정으로 로그인
          </button>
          <p className="login-hint">조직 SSO 연결 후 사용할 수 있습니다.</p>
        </div>
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
      </section>
    </main>
  );
}
