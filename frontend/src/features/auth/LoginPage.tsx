import { useEffect, useState } from "react";

import { Button } from "../../ds/Button";
import { Icon } from "../../ds/icons/Icon";
import { getAuthProviders, login, type AuthProviders } from "../../lib/api";
import { personName } from "../../lib/labels";
import type { OrganizationProfile } from "../../lib/viewModels";

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
                    {/* 바퀴 8-A: 구 `.avatar xs` 대신 새 DS 의 사람 아바타를 쓴다.
                        바퀴 12: main(#10)이 이 자리를 36px 로 키우며 `.login-account-avatar` 를 더했다 —
                        크기 클래스만 받고 아바타 자체는 DS 것을 지킨다(M-5). */}
                    <span aria-hidden className="scax-person-chip__avatar scax-person-chip__avatar--empty login-account-avatar">
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
            <div className="scax-field">
              <label className="scax-field__label" htmlFor="login-email">
                이메일
              </label>
              <div className="scax-textfield">
                <input
                  autoComplete="username"
                  className="scax-textfield__input"
                  id="login-email"
                  onChange={(event) => setEmail(event.target.value)}
                  type="email"
                  value={email}
                />
              </div>
            </div>
            <div className="scax-field">
              <label className="scax-field__label" htmlFor="login-password">
                비밀번호
              </label>
              <div className="scax-textfield">
                <input
                  autoComplete="current-password"
                  className="scax-textfield__input"
                  id="login-password"
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  value={password}
                />
              </div>
            </div>
            <Button variant="solid" tone="primary" disabled={isWorking || !email.trim() || !password} type="submit">
              {isWorking ? "로그인 중…" : "로그인"}
            </Button>
          </form>
        )}
        <div className="login-sso">
          {/* 바퀴 12(M-5): main 이 구 `.btn h40` 으로 되들인 것을 새 DS 단추로 되돌린다 */}
          <Button className="login-oidc" disabled size="lg" title="Google 로그인은 조직 SSO 연결 뒤에 열립니다" type="button">
            <span aria-hidden className="google-mark">G</span> Google 계정으로 로그인
          </Button>
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
