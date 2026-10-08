import type { ReactNode } from "react";

import { Button } from "../../ds/Button";
import { Modal } from "../../ds/Modal";
import { settingsScreen as copy } from "../../lib/labels";

/**
 * 설정 화면 공용 부품 — 시안 `settings.v1.jsx` 의 Card · Field · LiveStrip · LiveState · ConfirmBox · 방 표식.
 * 상자·여백은 프로젝트/자료 화면 어휘(`styles/settings.css`)다.
 */

export function Card({ title, legend, end, note, children }: { title: string; legend?: ReactNode; end?: ReactNode; note?: ReactNode; children?: ReactNode }) {
  return (
    <section aria-label={title} className="scax-set-card">
      <header className="scax-set-card__head">
        <h2 className="scax-set-card__title">{title}</h2>
        {legend ? <span className="scax-set-card__legend">{legend}</span> : null}
        {end ? <div className="scax-set-card__end">{end}</div> : null}
      </header>
      {children}
      {note ? <p className="scax-set-card__note">{note}</p> : null}
    </section>
  );
}

/**
 * 켬/끔 스위치 — 시안 `settings.v1.jsx:70-75` 모양(`<button role="switch" aria-checked>` + `__knob`) · CSS `.scax-switch*`(40×22).
 * **DS 에 Toggle 부품이 없다**(`ds/FormControls.tsx:9`) — 쓰는 곳이 설정 알림 하나라 설정 부품으로 둔다(SPEC-011 §2.3).
 * DS 문서 Toggle 규격(36×20)과 크기가 다르다 — DS-gaps 후보로만 기록한다.
 */
export function Switch({ on, label, onToggle, disabled = false }: { on: boolean; label: string; onToggle: () => void; disabled?: boolean }) {
  return (
    <button
      aria-checked={on}
      aria-label={label}
      className={`scax-switch${on ? " scax-switch--on" : ""}`}
      disabled={disabled}
      onClick={onToggle}
      role="switch"
      type="button"
    >
      <span className="scax-switch__knob" />
    </button>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="scax-field">
      <span className="scax-field__label">{label}</span>
      {children}
    </label>
  );
}

/** 적재 상태 3칸 — 마지막 수집 · DB 적재 건수 · 수집(실시간 / 멈춤 — …). */
export function LiveStrip({ last, total, paused, pausedLabel = copy.pausedBroken }: { last: string; total: string; paused?: boolean; pausedLabel?: string }) {
  return (
    <div className="scax-set-sync">
      <div className="scax-set-sync__cell">
        <span className="scax-set-sync__key">{copy.lastSync}</span>
        <span className="scax-set-sync__val">{last}</span>
      </div>
      <div className="scax-set-sync__cell">
        <span className="scax-set-sync__key">{copy.total}</span>
        <span className="scax-set-sync__val">{total}</span>
      </div>
      <div className="scax-set-sync__cell">
        <span className="scax-set-sync__key">{copy.collect}</span>
        <span className={`scax-set-live${paused ? " scax-set-live--paused" : ""}`}>
          <span className="scax-set-live__dot" />
          {paused ? pausedLabel : copy.live}
        </span>
      </div>
    </div>
  );
}

export type LiveStatus = "live" | "backfill" | "broken" | "paused";

/** 줄 오른쪽 상태 — 실시간 / 과거 채우는 중 / 연결 끊김 — 다시 연결 / 멈춤 */
export function LiveState({ status, backfillLabel, onReconnect }: { status: LiveStatus; backfillLabel?: string; onReconnect?: () => void }) {
  if (status === "broken") {
    return (
      <span className="scax-set-state scax-set-state--broken">
        <span className="scax-set-state__dot" />
        {copy.broken}
        {onReconnect ? (
          <button className="scax-set-link" onClick={onReconnect} type="button">
            {copy.reconnect}
          </button>
        ) : null}
      </span>
    );
  }
  if (status === "paused") {
    return (
      <span className="scax-set-state scax-set-state--paused">
        <span className="scax-set-state__dot" />
        {copy.paused}
      </span>
    );
  }
  if (status === "backfill") {
    return (
      <span className="scax-set-state scax-set-state--backfill">
        <span className="scax-set-state__dot" />
        {backfillLabel}
      </span>
    );
  }
  return (
    <span className="scax-set-state scax-set-state--live">
      <span className="scax-set-state__dot" />
      {copy.live}
    </span>
  );
}

export type Confirm = { title: string; lines: readonly string[]; cta: string; run: () => Promise<void> | void };

/** 확인 창 — 연결 해제 · 방 빼기 · 기기 철회. DS Modal(sm) 골격. */
export function ConfirmBox({ confirm, busy, onClose, onConfirm }: { confirm: Confirm | null; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  if (!confirm) return null;
  return (
    <Modal
      closeLabel={copy.close}
      footer={
        <>
          <Button disabled={busy} label={copy.cancel} onClick={onClose} tone="neutral" variant="outlined" />
          <Button disabled={busy} label={confirm.cta} onClick={onConfirm} tone="danger" variant="solid" />
        </>
      }
      label={confirm.title}
      onClose={onClose}
      size="sm"
      title={confirm.title}
    >
      <ul className="scax-set-confirm">
        {confirm.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </Modal>
  );
}

/** 슬랙 방 표식 — `#` 공개 · `#` 비공개 · `@` DM · `∴` 그룹 DM (DS 아이콘에 자물쇠·말풍선이 없어 글자로 대신, DC-2). */
export function RoomMark({ type }: { type: string }) {
  const glyph = type === "dm" ? "@" : type === "group" ? "∴" : "#";
  return (
    <span aria-hidden className={`scax-set-row__mark scax-set-row__mark--${type}`}>
      {glyph}
    </span>
  );
}

export function KakaoMark({ type }: { type: string }) {
  return (
    <span aria-hidden className={`scax-set-row__mark scax-set-row__mark--kakao-${type}`}>
      {type === "direct" ? "1" : "∴"}
    </span>
  );
}

/** 서버의 슬랙 방 종류(`group_dm`)를 시안의 이름(`group`)으로. */
export const slackType = (type: string) => (type === "group_dm" ? "group" : type);

export function slackRoomMeta(type: string, members: number | null): string {
  const kind = slackType(type);
  if (kind === "channel") return "채널 · 공개";
  if (kind === "private") return "채널 · 비공개";
  if (kind === "dm") return "DM";
  return `그룹 DM${members ? ` · ${members}명` : ""}`;
}

const pad = (value: number) => String(value).padStart(2, "0");

/** 「10-06 09:12」 — 시안 적재 상태 칸의 시각 모양. 값이 없으면 「—」. */
export function syncedAt(at: string | null | undefined): string {
  if (!at) return copy.never;
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return copy.never;
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export const countText = (value: number) => value.toLocaleString("ko-KR");
