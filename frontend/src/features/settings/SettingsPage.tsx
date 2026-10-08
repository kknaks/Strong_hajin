import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import {
  disconnectIntegration,
  listDeviceTokens,
  listIntegrationRooms,
  listIntegrations,
  reconnectIntegration,
  removeIntegrationRoom,
  resetKakaoAccount,
  revokeDeviceToken,
  startIntegrationConnect,
  type ConnectStart,
} from "../../lib/api";
import { settingsScreen as copy } from "../../lib/labels";
import { hasKakaoCollector } from "../../lib/shell";
import type { DeviceToken, Integration, IntegrationRoom, OrganizationProfile } from "../../lib/viewModels";
import { useInboxStream } from "../inbox/inboxStream";
import { beginConsent } from "./consent";
import { MailSection, SlackSection } from "./IntegrationSections";
import { KakaoSection } from "./KakaoSection";
import { NotifySection } from "./NotifySection";
import { ProfileSection } from "./ProfileSection";
import { ConfirmBox, type Confirm } from "./settingsParts";
import { useViewDetail } from "../../lib/currentView";

/**
 * 「설정」 화면 (WORK-011 FE-b · SPEC-008 §2.3~2.6) — 좌 레일(설정 메뉴) + 본문(섹션 상자). **모달이 아니라 화면이다.**
 *
 * 메뉴: 연동(메일 · 슬랙 · 카카오톡) · 계정(프로필 설정 · 알림 설정). 알림 설정은 WORK-013 WP3-FE 가 열었다(SPEC-011 §2.3 · `NotifySection`).
 * OAuth 콜백은 `?surface=settings&tab=…&connect=ok|denied` 로 돌아온다(N-2) — 그 탭을 열고 결과를 알린 뒤 쿼리를 지운다.
 * 데스크톱에서는 동의가 OS 브라우저에서 끝나므로 창의 `focus`/`visibilitychange` 때 연동을 다시 읽는다(N-3).
 */

export type SettingsTab = "mail" | "slack" | "kakao" | "account" | "notify";

const MENU: ReadonlyArray<{ caption: string; items: ReadonlyArray<{ id: SettingsTab; label: string }> }> = [
  {
    caption: copy.groups.link,
    items: [
      { id: "mail", label: copy.menu.mail },
      { id: "slack", label: copy.menu.slack },
      { id: "kakao", label: copy.menu.kakao },
    ],
  },
  {
    caption: copy.groups.account,
    items: [
      { id: "account", label: copy.menu.account },
      { id: "notify", label: copy.menu.notify },
    ],
  },
];

function SetNav({ active, onSelect, counts }: { active: SettingsTab; onSelect: (tab: SettingsTab) => void; counts: Partial<Record<SettingsTab, number>> }) {
  return (
    <div className="scax-set-nav">
      {MENU.map((group) => (
        <div className="scax-set-nav__group" key={group.caption}>
          <p className="scax-set-nav__caption">{group.caption}</p>
          {group.items.map((item) => (
            <button
              aria-current={item.id === active ? "true" : undefined}
              aria-label={item.label}
              className={`scax-set-nav__item${item.id === active ? " scax-set-nav__item--on" : ""}`}
              key={item.id}
              onClick={() => onSelect(item.id)}
              type="button"
            >
              <span className="scax-set-nav__label">{item.label}</span>
              {counts[item.id] != null ? <span className="scax-set-nav__tail">{counts[item.id]}</span> : null}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

export function SettingsPage({
  session,
  initialTab = "mail",
  connectResult = null,
  onProfileImage,
  characterBusy,
  characterError,
  onChooseCharacter,
  onError,
  onNotice,
  onRegisterRails,
  onRegisterTitle,
}: {
  session: OrganizationProfile;
  initialTab?: SettingsTab;
  /** OAuth 콜백 결과(N-2) — 화면이 한 번 알린다. */
  connectResult?: "ok" | "denied" | "error" | null;
  onProfileImage: (url: string | null) => void;
  characterBusy: boolean;
  characterError: string | null;
  onChooseCharacter: (key: string) => void;
  onError: (message: string | null) => void;
  onNotice: (message: string | null) => void;
  onRegisterRails?: (rails: { left?: ReactNode; right?: ReactNode }) => void;
  onRegisterTitle?: (title: string | null) => void;
}) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  /* OS 알림의 「보고 있으면 생략」(SPEC-011 §2.5 ①) — 연동 끊김 알림의 대상은 그 연동 탭 */
  useViewDetail("settingsTab", tab);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [slackRooms, setSlackRooms] = useState<IntegrationRoom[]>([]);
  const [kakaoRooms, setKakaoRooms] = useState<IntegrationRoom[]>([]);
  const [tokens, setTokens] = useState<DeviceToken[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [inApp, setInApp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const announced = useRef(false);
  /** 콜백이 `connect=error`(토큰 교환 실패)로 돌아왔다 — 설정 화면 머리에 오류 배너로 선다(검수 W-1). */
  const [callbackFailed, setCallbackFailed] = useState(connectResult === "error");

  const mail = integrations.filter((item) => item.kind === "mail");
  const slack = integrations.find((item) => item.kind === "slack") ?? null;
  const kakao = integrations.find((item) => item.kind === "kakao") ?? null;

  const loadRooms = useCallback(async (list: Integration[]) => {
    const slackOne = list.find((item) => item.kind === "slack");
    const kakaoOne = list.find((item) => item.kind === "kakao");
    const [slackList, kakaoList] = await Promise.all([
      slackOne ? listIntegrationRooms(slackOne.id).catch(() => []) : Promise.resolve([]),
      kakaoOne ? listIntegrationRooms(kakaoOne.id).catch(() => []) : Promise.resolve([]),
    ]);
    setSlackRooms(slackList);
    setKakaoRooms(kakaoList);
  }, []);

  const loadTokens = useCallback(async () => {
    try {
      setTokens(await listDeviceTokens());
    } catch {
      setTokens([]);
    }
  }, []);

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setState("loading");
      try {
        const list = (await listIntegrations()).filter((item) => item.status !== "removed");
        setIntegrations(list);
        setState("ready");
        await loadRooms(list);
      } catch {
        if (!quiet) setState("error");
      }
    },
    [loadRooms],
  );

  useEffect(() => {
    void load();
    void loadTokens();
    void hasKakaoCollector().then(setInApp);
  }, [load, loadTokens]);

  /* 데스크톱 — 동의는 OS 브라우저에서 끝난다. 창으로 돌아오면 다시 읽는다(N-3). */
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") void load(true);
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);

  /* 연동 상태가 바뀌면(백필 끝·끊김·수집기 상태) 사용자 사건이 온다 */
  useInboxStream(
    (event) => {
      if (event.type === "integration.changed") void load(true);
    },
    { onReconnect: () => void load(true) },
  );

  useEffect(() => {
    if (announced.current || !connectResult) return;
    announced.current = true;
    if (connectResult === "ok") onNotice(copy.connectOk);
    else if (connectResult === "denied") onError(copy.connectDenied);
  }, [connectResult, onError, onNotice]);

  const counts: Partial<Record<SettingsTab, number>> = { mail: mail.length, slack: slackRooms.length, kakao: kakaoRooms.length };

  useEffect(() => {
    if (!onRegisterRails) return;
    onRegisterRails({
      left: (
        <section aria-label={copy.rail} className="scax-gutter-list">
          <header className="scax-gutter-list__header">
            <h2 className="scax-gutter-list__title">
              <Icon name="setting" size={24} />
              {copy.rail}
            </h2>
          </header>
          <div className="scax-gutter-list__body">
            <SetNav active={tab} counts={counts} onSelect={setTab} />
          </div>
        </section>
      ),
    });
    return () => onRegisterRails({});
    // counts 는 매 렌더 새 객체라 숫자 셋으로 의존한다
  }, [counts.kakao, counts.mail, counts.slack, onRegisterRails, tab]);

  useEffect(() => {
    if (!onRegisterTitle) return;
    onRegisterTitle(copy.menu[tab]);
    return () => onRegisterTitle(null);
  }, [onRegisterTitle, tab]);

  const consent = async (start: () => Promise<ConnectStart>) => {
    setBusy(true);
    try {
      const outcome = await beginConsent(await start());
      if (outcome === "failed") onError(copy.connectFailed);
    } catch (reason) {
      onError(reason instanceof Error && reason.message ? reason.message : copy.connectFailed);
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      await confirm.run();
      setConfirm(null);
    } catch (reason) {
      onError(reason instanceof Error && reason.message ? reason.message : copy.removeFailed);
    } finally {
      setBusy(false);
    }
  };

  const askRemoveRoom = (integration: Integration | null, room: IntegrationRoom, kakaoRoom: boolean) => {
    if (!integration) return;
    setConfirm({
      title: copy.roomRemoveTitle,
      lines: kakaoRoom ? copy.kakaoRoomRemoveLines(room.name) : copy.roomRemoveLines(room.name),
      cta: copy.remove,
      run: async () => {
        await removeIntegrationRoom(integration.id, room.room_id);
        await load(true);
      },
    });
  };

  let body: ReactNode;
  if (tab === "notify") {
    body = <NotifySection onError={(message) => onError(message)} />;
  } else if (tab === "account") {
    body = (
      <ProfileSection
        characterBusy={characterBusy}
        characterError={characterError}
        onChooseCharacter={onChooseCharacter}
        onNotice={(message) => onNotice(message)}
        onProfileImage={onProfileImage}
        session={session}
      />
    );
  } else if (state === "loading") {
    body = (
      <div aria-busy="true" className="scax-skeleton-stack" role="status">
        {[0, 1, 2].map((index) => (
          <span aria-hidden className="scax-skeleton scax-skeleton--card" key={index} />
        ))}
      </div>
    );
  } else if (state === "error") {
    body = <Empty actionLabel={copy.retry} description={copy.retryDesc} onAction={() => void load()} title={copy.loadError} variant="error" />;
  } else if (tab === "mail") {
    body = (
      <MailSection
        accounts={mail}
        busy={busy}
        onAskRemove={(account) =>
          setConfirm({
            title: copy.mailRemoveTitle,
            lines: copy.mailRemoveLines(account.display_name),
            cta: copy.disconnect,
            run: async () => {
              await disconnectIntegration(account.id);
              await load(true);
            },
          })
        }
        onConnect={() => void consent(() => startIntegrationConnect("mail"))}
        onReconnect={(account) => void consent(() => reconnectIntegration(account.id))}
      />
    );
  } else if (tab === "slack") {
    body = (
      <SlackSection
        busy={busy}
        integration={slack}
        onAskDisconnect={(integration) =>
          setConfirm({
            title: copy.slackDisconnectTitle,
            lines: copy.slackDisconnectLines(integration.display_name || copy.slackWorkspace),
            cta: copy.disconnect,
            run: async () => {
              await disconnectIntegration(integration.id);
              await load(true);
            },
          })
        }
        onAskRemove={(room) => askRemoveRoom(slack, room, false)}
        onConnect={() => void consent(() => startIntegrationConnect("slack"))}
        onError={(message) => onError(message)}
        onReconnect={(integration) => void consent(() => reconnectIntegration(integration.id))}
        onRoomsChanged={() => void load(true)}
        rooms={slackRooms}
      />
    );
  } else {
    body = (
      <KakaoSection
        busy={busy}
        inApp={inApp}
        integration={kakao}
        onAskRemove={(room) => askRemoveRoom(kakao, room, true)}
        onAskRevoke={(token) =>
          setConfirm({
            title: copy.deviceRevokeTitle,
            lines: copy.deviceRevokeLines(token.device_name),
            cta: copy.deviceRevoke,
            run: async () => {
              await revokeDeviceToken(token.id);
              await loadTokens();
            },
          })
        }
        onError={(message) => onError(message)}
        onNotice={(message) => onNotice(message)}
        onResetAccount={() => {
          setBusy(true);
          resetKakaoAccount()
            .then(() => load(true))
            .catch((reason: unknown) => onError(reason instanceof Error && reason.message ? reason.message : copy.connectFailed))
            .finally(() => setBusy(false));
        }}
        onRoomsChanged={() => void load(true)}
        onTokensChanged={() => void loadTokens()}
        rooms={kakaoRooms}
        tokens={tokens}
      />
    );
  }

  return (
    <div className="scax-set-view">
      {callbackFailed ? (
        <div className="scax-inbox-notice scax-inbox-notice--danger" role="alert">
          <span className="scax-inbox-notice__text">{copy.connectError}</span>
          <Button label={copy.close} onClick={() => setCallbackFailed(false)} size="sm" tone="neutral" variant="outlined" />
        </div>
      ) : null}
      {body}
      <ConfirmBox busy={busy} confirm={confirm} onClose={() => setConfirm(null)} onConfirm={() => void run()} />
    </div>
  );
}
