import { useEffect } from "react";

import type { NotificationTarget, ProductSurface } from "./viewModels";

/**
 * **지금 무엇을 보고 있나** — OS 알림의 「보고 있으면 생략」(SPEC-011 §2.5 ① · D-39)이 쓰는 작은 기록.
 *
 * 화면은 자기가 연 상세를 `useViewDetail` 로 알린다(업무 · 요청 상세 · 메일 · 방 · 회의 · 설정 탭). 화면이 내려가면 지운다.
 * 화면 종류(`surface`)는 App 이 안다 — `isViewingTarget(target, surface)` 가 둘을 맞춘다. 화면을 다시 그리지 않는다(상태가 아니다).
 */
export type ViewDetailKey = "task" | "workRequest" | "meeting" | "inboxMail" | "inboxRoom" | "settingsTab";

const details = new Map<ViewDetailKey, string>();

export function setViewDetail(key: ViewDetailKey, value: string | null) {
  if (value) details.set(key, value);
  else details.delete(key);
}

/** 이 화면이 지금 연 상세를 알린다 — 값이 바뀌면 고치고, 화면이 내려가면 지운다. */
export function useViewDetail(key: ViewDetailKey, value: string | null | undefined) {
  useEffect(() => {
    setViewDetail(key, value ?? null);
    return () => {
      if (details.get(key) === (value ?? undefined)) details.delete(key);
    };
  }, [key, value]);
}

/**
 * 그 알림의 대상을 지금 보고 있나 — 업무 = 업무 화면에서 그 업무(또는 요청) 상세 · 메일 · 방 = 메시지함에서 그것 · 회의 = 그 회의 ·
 * 연동 = 설정의 그 탭. **알림 목록 화면을 보고 있는 것은 아니다**(OQ-1111).
 */
export function isViewingTarget(target: NotificationTarget, surface: ProductSurface): boolean {
  switch (target.surface) {
    case "work":
      if (surface !== "work") return false;
      return target.task_id ? details.get("task") === target.task_id : details.get("workRequest") === target.work_request_id;
    case "inbox":
      if (surface !== "inbox") return false;
      return target.source === "mail" ? details.get("inboxMail") === target.message_id : details.get("inboxRoom") === target.room_id;
    case "meetings":
      return surface === "meetings" && details.get("meeting") === target.meeting_id;
    case "settings":
      return surface === "settings" && details.get("settingsTab") === target.tab;
    default:
      return false;
  }
}

/** 시험용 */
export function forgetViewDetails() {
  details.clear();
}
