import { useCallback, useEffect, useState } from "react";

import { Button } from "../../ds/Button";
import { Icon } from "../../ds/icons/Icon";
import { addIntegrationRooms, issueDeviceToken } from "../../lib/api";
import { settingsScreen as copy } from "../../lib/labels";
import { kakaoListRooms, kakaoStoreDeviceToken } from "../../lib/shell";
import type { DeviceToken, Integration, IntegrationRoom, KakaoLocalRoom } from "../../lib/viewModels";
import { RoomPicker, type PickState } from "./RoomPicker";
import { Card, countText, KakaoMark, LiveState, LiveStrip, syncedAt } from "./settingsParts";

/**
 * 설정 — 카카오톡 연동 (SPEC-008 §2.5 · DC-6 · R-F1).
 *
 * - **어디서 여느냐로 갈린다**: 방 «추가» 창은 **카톡 커맨드가 있는 데스크톱 앱 웹뷰에서만** 뜬다(방 목록이 Mac 로컬).
 *   브라우저면 「방 추가는 Mac 앱에서」 안내만. 고른 방 목록·빼기는 서버 정본이라 **둘 다 된다**.
 * - 수집 상태 카드는 수집기가 올린 상태(§4.6 status)를 그대로 비춘다 — 앱 꺼짐(90초 무보고) · 카톡 꺼짐 · 읽기 불가(사유별
 *   문구) · 계정 바뀜(「다시 연결」 = `reset-account`).
 * - 「이 Mac 연결」 — 기기 토큰을 발급받아 **곧바로** `kakao_store_device_token` 으로 넘긴다. 토큰은 화면·저장소에 남지 않는다.
 * - 카톡 연동 레코드는 수집기의 첫 handshake 가 만든다 — 없으면 「Mac 앱 받기」 카드다.
 */

type Mode = "no-app" | "app-off" | "kakao-off" | "unreadable" | "live";

export function kakaoMode(integration: Integration | null): Mode {
  if (!integration) return "no-app";
  const collector = integration.collector;
  if (!collector || collector.app_state !== "on") return "app-off";
  if (collector.kakao_state === "off") return "kakao-off";
  if (collector.kakao_state === "unreadable") return "unreadable";
  return "live";
}

function KakaoCell({ label, value, tone, sub }: { label: string; value: string; tone: "on" | "off" | "idle" | "plain"; sub?: string | null }) {
  return (
    <div className="scax-set-kstat">
      <span className="scax-set-kstat__label">{label}</span>
      <span className={`scax-set-kstat__value scax-set-kstat__value--${tone}`}>
        <span className="scax-set-state__dot" />
        {value}
      </span>
      {sub ? <span className="scax-set-kstat__sub">{sub}</span> : null}
    </div>
  );
}

/** 카톡 방 추가 창 — 목록은 **데스크톱 앱이 로컬 카톡 DB 에서** 읽어 준다(Tauri 커맨드). 고른 결과는 서버에 저장. */
function KakaoPicker({ integration, rooms, onClose, onAdded, onError }: { integration: Integration; rooms: IntegrationRoom[]; onClose: () => void; onAdded: () => void; onError: (message: string) => void }) {
  const [list, setList] = useState<KakaoLocalRoom[]>([]);
  const [state, setState] = useState<PickState>("loading");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setState("loading");
    const outcome = await kakaoListRooms();
    if (outcome.kind === "ok") {
      setList(outcome.rooms);
      setState("ready");
    } else if (outcome.kind === "kakao_off") setState({ kind: "notice", title: copy.kakaoPickOff, desc: copy.kakaoPickOffDesc });
    else if (outcome.kind === "absent") setState({ kind: "notice", title: copy.kakaoPickAbsent, desc: copy.kakaoPickOffDesc });
    else setState("error");
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const added = new Set(rooms.map((room) => room.external_id));
  const add = async (ids: string[]) => {
    setBusy(true);
    try {
      const details = list.filter((room) => ids.includes(room.chat_id)).map((room) => ({ room_id: room.chat_id, type: room.type, name: room.name, member_count: room.member_count }));
      await addIntegrationRooms(integration.id, ids, details);
      onAdded();
      onClose();
    } catch (reason) {
      onError(reason instanceof Error && reason.message ? reason.message : copy.addFailed);
    } finally {
      setBusy(false);
    }
  };
  return (
    <RoomPicker
      busy={busy}
      errorDesc={copy.kakaoPickError}
      groupLabel={(group) => copy.kakaoPickTabs.find((tab) => tab.value === group)?.label ?? group}
      groupOrder={["direct", "group"] as const}
      hint={copy.kakaoPickHint}
      onAdd={(ids) => void add(ids)}
      onClose={onClose}
      onQuery={setQuery}
      onRetry={() => void load()}
      query={query}
      rows={list.map((room) => ({
        id: room.chat_id,
        group: room.type,
        name: room.name,
        meta: room.type === "direct" ? "1:1" : room.member_count != null ? copy.joined(room.member_count) : "",
        added: added.has(room.chat_id),
        mark: <KakaoMark type={room.type} />,
      }))}
      state={state}
      tabs={copy.kakaoPickTabs}
      title={copy.kakaoPickTitle}
    />
  );
}

/** 기기 연결 — 시안에 없는 자리(DS-gaps). 같은 카드·줄 어휘로 섰다. 브라우저에선 목록·철회만 된다. */
function DeviceCard({
  inApp,
  tokens,
  deviceName,
  onChanged,
  onAskRevoke,
  onNotice,
  onError,
}: {
  inApp: boolean;
  tokens: DeviceToken[];
  deviceName: string;
  onChanged: () => void;
  onAskRevoke: (token: DeviceToken) => void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const connect = async () => {
    setBusy(true);
    try {
      /* 원문 토큰은 이 함수 안에서만 산다 — 받자마자 셸로 넘기고 버린다(P-5 · AC-14c). */
      const stored = await kakaoStoreDeviceToken((await issueDeviceToken(deviceName)).token);
      if (stored === "stored") onNotice(copy.deviceStored);
      else onError(copy.deviceStoreFailed);
      onChanged();
    } catch (reason) {
      onError(reason instanceof Error && reason.message ? reason.message : copy.deviceFailed);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      end={inApp ? <Button disabled={busy} label={copy.deviceConnect} onClick={() => void connect()} size="sm" tone="primary" variant="solid" /> : null}
      legend={copy.count(tokens.length)}
      note={inApp ? copy.deviceDesc : copy.deviceInApp}
      title={copy.device}
    >
      {tokens.length ? (
        <ul className="scax-set-list">
          {tokens.map((token) => (
            <li className="scax-set-row" key={token.id}>
              <span aria-hidden className="scax-set-row__mark scax-set-row__mark--kakao-direct">
                <Icon name="link" size={16} />
              </span>
              <span className="scax-set-row__who">
                <span className="scax-set-row__name">{token.device_name}</span>
                <span className="scax-set-row__meta">{copy.deviceMeta(syncedAt(token.created_at), token.last_used_at ? syncedAt(token.last_used_at) : copy.deviceNeverUsed)}</span>
              </span>
              <span className="scax-set-row__end">
                <Button label={copy.deviceRevoke} onClick={() => onAskRevoke(token)} size="sm" tone="neutral" variant="text" />
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="scax-set-card__note">{copy.deviceNone}</p>
      )}
    </Card>
  );
}

export function KakaoSection({
  integration,
  rooms,
  tokens,
  inApp,
  busy,
  onAskRemove,
  onRoomsChanged,
  onTokensChanged,
  onAskRevoke,
  onResetAccount,
  onNotice,
  onError,
}: {
  integration: Integration | null;
  rooms: IntegrationRoom[];
  tokens: DeviceToken[];
  /** 카톡 커맨드가 있는 데스크톱 앱 웹뷰인가(`hasKakaoCollector`). */
  inApp: boolean;
  busy: boolean;
  onAskRemove: (room: IntegrationRoom) => void;
  onRoomsChanged: () => void;
  onTokensChanged: () => void;
  onAskRevoke: (token: DeviceToken) => void;
  onResetAccount: () => void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const mode = kakaoMode(integration);
  const collector = integration?.collector ?? null;
  const intro = <p className="scax-set-view__intro">{copy.kakaoIntro}</p>;
  const deviceName = collector?.device_name || "Mac";
  const device = (
    <DeviceCard deviceName={deviceName} inApp={inApp} onAskRevoke={onAskRevoke} onChanged={onTokensChanged} onError={onError} onNotice={onNotice} tokens={tokens} />
  );

  if (mode === "no-app" || !integration) {
    return (
      <>
        {intro}
        <Card title={copy.desktopApp}>
          <div className="scax-set-connect">
            <p className="scax-set-connect__desc">{copy.noAppDesc}</p>
            <a className="scax-set-applink" href={copy.downloadAppUrl} rel="noreferrer" target="_blank">
              <Icon name="arrow-down" size={16} />
              {copy.downloadApp}
            </a>
          </div>
        </Card>
        {inApp || tokens.length ? device : null}
      </>
    );
  }

  const appOn = mode !== "app-off";
  const paused = mode !== "live" || Boolean(collector?.account_changed);
  const pausedLabel = !appOn ? copy.pausedAppOff : copy.pausedKakaoOff;
  const unreadableText = collector?.kakao_reason === "permission" ? copy.unreadablePermission : copy.unreadableVersion;
  const kakaoValue = !appOn ? copy.kakaoUnknown : mode === "kakao-off" ? copy.kakaoOff : mode === "unreadable" ? copy.kakaoUnreadable : copy.kakaoRunning;
  const kakaoTone = !appOn ? "idle" : mode === "live" ? "on" : "off";
  const kakaoSub = !appOn ? copy.kakaoUnknownSub : mode === "kakao-off" ? copy.kakaoOffSub : mode === "unreadable" ? unreadableText : copy.kakaoRunningSub;
  const appSub = appOn ? [collector?.device_name, collector?.app_version ? `v${collector.app_version}` : null].filter(Boolean).join(" · ") : copy.appOffSub(syncedAt(collector?.reported_at));

  return (
    <>
      {intro}
      <Card legend={paused ? copy.paused : copy.live} note={copy.accountNote} title={copy.collectState}>
        <div className="scax-set-kstats">
          <KakaoCell label={copy.desktopApp} sub={appSub} tone={appOn ? "on" : "off"} value={appOn ? copy.appOn : copy.appOff} />
          <KakaoCell label={copy.kakao} sub={kakaoSub} tone={kakaoTone} value={kakaoValue} />
          <KakaoCell label={copy.account} tone="plain" value={collector?.account_name || copy.never} />
          <KakaoCell label={copy.lastSync} sub={paused ? copy.pausedSub : copy.liveSub} tone="plain" value={syncedAt(integration.last_synced_at)} />
        </div>
        {collector?.account_changed ? (
          <div className="scax-inbox-notice scax-inbox-notice--danger" role="alert">
            <span className="scax-inbox-notice__text">{copy.accountChanged}</span>
            <Button disabled={busy} label={copy.reconnect} onClick={onResetAccount} size="sm" tone="neutral" variant="outlined" />
          </div>
        ) : null}
      </Card>
      <Card
        end={inApp ? <Button disabled={busy} iconBefore="plus" label={copy.addRoom} onClick={() => setPicking(true)} size="sm" tone="primary" variant="solid" /> : null}
        legend={copy.count(rooms.length)}
        title={copy.rooms}
      >
        <LiveStrip last={syncedAt(integration.last_synced_at)} paused={paused} pausedLabel={pausedLabel} total={countText(integration.synced_count)} />
        {rooms.length ? (
          <ul className="scax-set-list">
            {rooms.map((room) => (
              <li className="scax-set-row" key={room.room_id}>
                <KakaoMark type={room.type} />
                <span className="scax-set-row__who">
                  <span className="scax-set-row__name">{room.name}</span>
                  <span className="scax-set-row__meta">{copy.roomMeta(copy.kakaoMeta(room.type, room.member_count), countText(room.synced_count))}</span>
                </span>
                <span className="scax-set-row__end">
                  <LiveState backfillLabel={copy.kakaoBackfill} status={paused ? "paused" : room.status === "backfilling" ? "backfill" : "live"} />
                  <Button disabled={busy} label={copy.remove} onClick={() => onAskRemove(room)} size="sm" tone="neutral" variant="text" />
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="scax-set-card__note">{copy.noRooms}</p>
        )}
        <p className="scax-set-card__note">{inApp ? copy.kakaoRoomsNote : copy.kakaoAddInApp}</p>
      </Card>
      {device}
      {picking ? <KakaoPicker integration={integration} onAdded={onRoomsChanged} onClose={() => setPicking(false)} onError={onError} rooms={rooms} /> : null}
    </>
  );
}
