import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../../ds/Button";
import { addIntegrationRooms, listSlackAvailableRooms } from "../../lib/api";
import { settingsScreen as copy } from "../../lib/labels";
import type { Integration, IntegrationRoom, SlackAvailableRoom } from "../../lib/viewModels";
import { RoomPicker, type PickState } from "./RoomPicker";
import { Card, countText, LiveState, LiveStrip, RoomMark, slackRoomMeta, slackType, syncedAt, type LiveStatus } from "./settingsParts";

/**
 * 설정 — 메일 연동 · 슬랙 연동 (SPEC-008 §2.3·2.4 · DC-2).
 *
 * 둘 다 **사용자 한 번 연결 → 실시간 수집**이다. 연결 시작은 서버가 준 동의 URL 로(F-2 — `consent.ts`),
 * 해제는 소프트 딜리트(D-27). 메일은 계정이 여럿(D-10), 슬랙은 워크스페이스 하나 + 고른 방(D-11).
 */

export const statusOf = (status: Integration["status"]): LiveStatus =>
  status === "disconnected" ? "broken" : status === "backfilling" ? "backfill" : "live";

const roomStatusOf = (status: string): LiveStatus => (status === "backfilling" ? "backfill" : status === "paused" ? "paused" : "live");

function lastOf(list: Array<{ last_synced_at: string | null }>): string | null {
  return list.reduce<string | null>((latest, item) => (item.last_synced_at && (!latest || item.last_synced_at > latest) ? item.last_synced_at : latest), null);
}

/* ===== 메일 ===== */

export function MailSection({
  accounts,
  busy,
  onConnect,
  onReconnect,
  onAskRemove,
}: {
  accounts: Integration[];
  busy: boolean;
  onConnect: () => void;
  onReconnect: (account: Integration) => void;
  onAskRemove: (account: Integration) => void;
}) {
  const intro = <p className="scax-set-view__intro">{copy.mailIntro}</p>;
  if (!accounts.length) {
    return (
      <>
        {intro}
        <Card title={copy.mailConnectTitle}>
          <div className="scax-set-connect">
            <p className="scax-set-connect__desc">{copy.mailConnectDesc}</p>
            <Button disabled={busy} label={copy.mailConnect} onClick={onConnect} tone="primary" variant="solid" />
          </div>
        </Card>
      </>
    );
  }
  const broken = accounts.some((account) => account.status === "disconnected");
  const total = accounts.reduce((sum, account) => sum + account.synced_count, 0);
  return (
    <>
      {intro}
      <Card legend={copy.count(accounts.length)} title={copy.mailList}>
        <LiveStrip last={syncedAt(lastOf(accounts))} paused={accounts.every((account) => account.status === "disconnected")} total={countText(total)} />
        <ul className="scax-set-list">
          {accounts.map((account) => (
            <li className={`scax-set-row${account.status === "disconnected" ? " scax-set-row--broken" : ""}`} key={account.id}>
              <span aria-hidden className="scax-set-row__mark scax-set-row__mark--gmail">
                G
              </span>
              <span className="scax-set-row__who">
                <span className="scax-set-row__name">{account.display_name}</span>
                <span className="scax-set-row__meta">{copy.mailMeta(countText(account.synced_count), syncedAt(account.last_synced_at))}</span>
              </span>
              <span className="scax-set-row__end">
                <LiveState backfillLabel={copy.mailBackfill(countText(account.backfill_count))} onReconnect={() => onReconnect(account)} status={statusOf(account.status)} />
                <Button disabled={busy} label={copy.disconnect} onClick={() => onAskRemove(account)} size="sm" tone="neutral" variant="text" />
              </span>
            </li>
          ))}
        </ul>
        {broken ? <p className="scax-set-card__note scax-set-card__note--danger">{copy.mailBrokenNote}</p> : null}
        <div className="scax-set-foot scax-set-foot--start">
          <Button disabled={busy} iconBefore="plus" label={copy.mailAnother} onClick={onConnect} tone="neutral" variant="outlined" />
        </div>
      </Card>
    </>
  );
}

/* ===== 슬랙 ===== */

/** 슬랙 방 고르기 창 — 서버가 사용자 토큰으로 그때 조회한다(그룹 DM 은 참여자 실명 · 봇 DM 도 DM 에 섞임, D-12·13). */
function SlackPicker({ integration, rooms, onClose, onAdded, onError }: { integration: Integration; rooms: IntegrationRoom[]; onClose: () => void; onAdded: () => void; onError: (message: string) => void }) {
  const [query, setQuery] = useState("");
  const [list, setList] = useState<SlackAvailableRoom[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<PickState>("loading");
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async (q: string, after?: string | null) => {
    const mine = ++seq.current;
    if (!after) setState("loading");
    try {
      const page = await listSlackAvailableRooms(q, after);
      if (mine !== seq.current) return;
      setList((current) => (after ? [...current, ...page.rooms] : page.rooms));
      setCursor(page.next_cursor);
      setState("ready");
    } catch {
      if (mine === seq.current) setState("error");
    }
  }, []);

  /* 검색은 서버가 한다 — 치는 동안은 기다렸다가 한 번 */
  useEffect(() => {
    const timer = window.setTimeout(() => void load(query), query ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [load, query]);

  const added = new Set(rooms.map((room) => room.external_id));
  const add = async (ids: string[]) => {
    setBusy(true);
    try {
      const details = list
        .filter((room) => ids.includes(room.room_id))
        .map((room) => ({ room_id: room.room_id, type: room.type, name: room.name, member_count: room.member_count, is_bot: room.is_bot }));
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
      errorDesc={copy.pickErrorDesc}
      groupLabel={(group) => copy.roomType[group] ?? group}
      groupOrder={["channel", "private", "dm", "group"] as const}
      hasMore={Boolean(cursor)}
      hint={copy.pickHint}
      onAdd={(ids) => void add(ids)}
      onClose={onClose}
      onMore={() => void load(query, cursor)}
      onQuery={setQuery}
      onRetry={() => void load(query)}
      query={query}
      rows={list.map((room) => {
        const group = slackType(room.type);
        return {
          id: room.room_id,
          group,
          name: room.name,
          isBot: room.is_bot,
          meta: group === "dm" ? "DM" : room.member_count != null ? copy.people(room.member_count) : "",
          added: room.already_added || added.has(room.room_id),
          mark: <RoomMark type={group} />,
        };
      })}
      state={state}
      tabs={copy.pickTabs}
      title={copy.pickTitle}
    />
  );
}

export function SlackSection({
  integration,
  rooms,
  busy,
  onConnect,
  onReconnect,
  onAskDisconnect,
  onAskRemove,
  onRoomsChanged,
  onError,
}: {
  integration: Integration | null;
  rooms: IntegrationRoom[];
  busy: boolean;
  onConnect: () => void;
  onReconnect: (integration: Integration) => void;
  onAskDisconnect: (integration: Integration) => void;
  onAskRemove: (room: IntegrationRoom) => void;
  onRoomsChanged: () => void;
  onError: (message: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const intro = <p className="scax-set-view__intro">{copy.slackIntro}</p>;
  if (!integration) {
    return (
      <>
        {intro}
        <Card title={copy.slackWorkspace}>
          <div className="scax-set-connect">
            <p className="scax-set-connect__desc">{copy.slackConnectDesc}</p>
            <Button disabled={busy} label={copy.slackConnect} onClick={onConnect} tone="primary" variant="solid" />
          </div>
        </Card>
      </>
    );
  }
  const broken = integration.status === "disconnected";
  const name = integration.display_name || copy.slackWorkspace;
  return (
    <>
      {intro}
      <Card
        end={<Button disabled={busy} label={copy.disconnect} onClick={() => onAskDisconnect(integration)} size="sm" tone="neutral" variant="outlined" />}
        legend={broken ? copy.broken : copy.connected}
        title={copy.slackWorkspace}
      >
        <div className={`scax-set-row${broken ? " scax-set-row--broken" : ""}`}>
          <span aria-hidden className="scax-set-row__mark scax-set-row__mark--slack">
            {name.slice(0, 1)}
          </span>
          <span className="scax-set-row__who">
            <span className="scax-set-row__name">{name}</span>
          </span>
          <span className="scax-set-row__end">
            <LiveState backfillLabel={copy.slackBackfill} onReconnect={() => onReconnect(integration)} status={broken ? "broken" : "live"} />
          </span>
        </div>
        {broken ? <p className="scax-set-card__note scax-set-card__note--danger">{copy.slackBrokenNote}</p> : null}
      </Card>
      <Card
        end={<Button disabled={busy || broken} iconBefore="plus" label={copy.addRoom} onClick={() => setPicking(true)} size="sm" tone="primary" variant="solid" />}
        legend={copy.count(rooms.length)}
        title={copy.rooms}
      >
        <LiveStrip last={syncedAt(integration.last_synced_at)} paused={broken} total={countText(integration.synced_count)} />
        {rooms.length ? (
          <ul className="scax-set-list">
            {rooms.map((room) => (
              <li className="scax-set-row" key={room.room_id}>
                <RoomMark type={slackType(room.type)} />
                <span className="scax-set-row__who">
                  <span className="scax-set-row__name">{room.name}</span>
                  <span className="scax-set-row__meta">{copy.roomMeta(slackRoomMeta(room.type, room.member_count), countText(room.synced_count))}</span>
                </span>
                <span className="scax-set-row__end">
                  <LiveState backfillLabel={copy.slackBackfill} status={broken ? "paused" : roomStatusOf(room.status)} />
                  <Button disabled={busy} label={copy.remove} onClick={() => onAskRemove(room)} size="sm" tone="neutral" variant="text" />
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="scax-set-card__note">{copy.noRooms}</p>
        )}
        <p className="scax-set-card__note">{copy.slackRoomsNote}</p>
      </Card>
      {picking ? <SlackPicker integration={integration} onAdded={onRoomsChanged} onClose={() => setPicking(false)} onError={onError} rooms={rooms} /> : null}
    </>
  );
}
