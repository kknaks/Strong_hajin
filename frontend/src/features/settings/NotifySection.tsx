import { useCallback, useEffect, useRef, useState } from "react";

import { CheckboxBox } from "../../ds/FormControls";
import { Empty } from "../../ds/Empty";
import { ApiError, getNotificationSettings, saveNotificationSettings } from "../../lib/api";
import { notifySettingsCopy as copy, settingsScreen } from "../../lib/labels";
import type { NotificationSettings, NotificationTheme } from "../../lib/viewModels";
import { Switch } from "./settingsParts";

/**
 * 설정 → 알림 설정 (WORK-013 WP3-FE · SPEC-011 §2.3 · §4.4 — 시안 `settings.v1.jsx` `NotifySection` 그대로).
 *
 * 맨 위 「알림 받기」 → 테마 카드 셋(머리 스위치 · 「N개 중 M개」/「꺼짐」) → 항목 체크. 전체를 끄면 테마 머리까지 흐려지고,
 * 테마를 끄면 그 항목들이 흐려진다 — **아래 값은 그대로 남는다**(D-18).
 *
 * **저장**: 바꿀 때마다 바로 저장한다(저장 단추 없음). 저장 중인 그 칸만 잠근다. 서버는 전체를 `version` 과 함께 받으므로
 * 저장은 **한 줄로 세운다**(앞 저장의 새 `version` 으로 뒤 저장을 보낸다). 실패하면 그 칸만 되돌리고 공통 토스트
 * 「알림 설정을 저장하지 못했습니다」 · `409`(다른 창이 먼저 저장)면 서버 값을 다시 읽는다.
 */

type Change =
  | { kind: "enabled"; value: boolean }
  | { kind: "theme"; theme: NotificationTheme; value: boolean }
  | { kind: "item"; theme: NotificationTheme; item: string; value: boolean };

const keyOf = (change: Change) => (change.kind === "enabled" ? "enabled" : change.kind === "theme" ? `theme:${change.theme}` : `item:${change.theme}.${change.item}`);

function apply(settings: NotificationSettings, change: Change): NotificationSettings {
  if (change.kind === "enabled") return { ...settings, enabled: change.value };
  const theme = settings.themes[change.theme];
  if (change.kind === "theme") return { ...settings, themes: { ...settings.themes, [change.theme]: { ...theme, on: change.value } } };
  return { ...settings, themes: { ...settings.themes, [change.theme]: { ...theme, items: { ...theme.items, [change.item]: change.value } } } };
}

const invert = (change: Change): Change => ({ ...change, value: !change.value }) as Change;

export function NotifySection({ onError }: { onError: (message: string) => void }) {
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState<string[]>([]);
  const current = useRef<NotificationSettings | null>(null);
  const version = useRef(0);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const pending = useRef(0);
  const alive = useRef(true);
  useEffect(() => () => {
    alive.current = false;
  }, []);

  const put = useCallback((next: NotificationSettings) => {
    current.current = next;
    setSettings(next);
  }, []);

  const load = useCallback(async () => {
    try {
      const loaded = await getNotificationSettings();
      if (!alive.current) return;
      version.current = loaded.version;
      put(loaded);
      setState("ready");
    } catch {
      if (alive.current && !current.current) setState("error");
    }
  }, [put]);

  useEffect(() => {
    void load();
  }, [load]);

  const change = (next: Change) => {
    if (!current.current) return;
    const key = keyOf(next);
    put(apply(current.current, next));
    setSaving((list) => [...list, key]);
    pending.current += 1;
    queue.current = queue.current.then(async () => {
      try {
        const snapshot = current.current;
        if (!snapshot) return;
        const saved = await saveNotificationSettings({ ...snapshot, version: version.current });
        version.current = saved.version;
        // 뒤에 줄 선 저장이 없으면 서버 값을 정본으로 — 있으면 화면의 값(그 뒤 바꾼 것)을 지킨다
        if (alive.current && pending.current === 1) put(saved);
      } catch (reason) {
        if (!alive.current) return;
        onError(copy.saveError);
        if (reason instanceof ApiError && reason.status === 409) await load();
        else if (current.current) put(apply(current.current, invert(next)));
      } finally {
        pending.current -= 1;
        if (alive.current) setSaving((list) => {
          const index = list.indexOf(key);
          return index < 0 ? list : [...list.slice(0, index), ...list.slice(index + 1)];
        });
      }
    });
  };

  if (state === "loading") {
    return (
      <div aria-busy="true" className="scax-skeleton-stack" role="status">
        {[0, 1, 2].map((index) => (
          <span aria-hidden className="scax-skeleton scax-skeleton--card" key={index} />
        ))}
      </div>
    );
  }
  if (state === "error" || !settings) {
    return <Empty actionLabel={settingsScreen.retry} description={settingsScreen.retryDesc} onAction={() => { setState("loading"); void load(); }} title={copy.loadError} variant="error" />;
  }

  const master = settings.enabled;
  return (
    <>
      <p className="scax-set-view__intro">{copy.intro}</p>
      <section aria-label={copy.master} className="scax-set-card scax-set-notify__master">
        <div className="scax-switch-row">
          <span className="scax-switch-row__who">
            <span className="scax-set-row__name">{copy.master}</span>
            <span className="scax-set-row__meta">{master ? copy.masterOn : copy.masterOff}</span>
          </span>
          <Switch disabled={saving.includes("enabled")} label={copy.master} on={master} onToggle={() => change({ kind: "enabled", value: !master })} />
        </div>
      </section>
      {copy.groups.map((group) => {
        const theme = settings.themes[group.id] ?? { on: true, items: {} };
        const themeOff = !master || !theme.on;
        const picked = group.items.filter((item) => theme.items[item.id]).length;
        return (
          <section aria-label={group.title} className={`scax-set-card scax-set-notify${master ? "" : " scax-set-notify--off"}`} key={group.id}>
            <header className="scax-set-card__head scax-set-notify__head">
              <h2 className="scax-set-card__title">{group.title}</h2>
              <span className="scax-set-card__legend">{theme.on ? copy.picked(group.items.length, picked) : copy.themeOff}</span>
              <Switch
                disabled={!master || saving.includes(`theme:${group.id}`)}
                label={copy.themeSwitch(group.title)}
                on={theme.on}
                onToggle={() => change({ kind: "theme", theme: group.id, value: !theme.on })}
              />
            </header>
            <ul className={`scax-set-notify__list${themeOff ? " scax-set-notify__list--off" : ""}`}>
              {group.items.map((item) => {
                const on = Boolean(theme.items[item.id]);
                return (
                  <li key={item.id}>
                    <label className={`scax-set-notify__item${themeOff ? " scax-set-notify__item--off" : ""}`}>
                      <CheckboxBox
                        checked={on}
                        disabled={themeOff || saving.includes(`item:${group.id}.${item.id}`)}
                        onChange={(value) => change({ kind: "item", theme: group.id, item: item.id, value })}
                      />
                      <span className="scax-set-row__who">
                        <span className="scax-set-row__name">{item.label}</span>
                        <span className="scax-set-row__meta">{item.desc}</span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
            {group.note ? <p className={`scax-set-card__note${themeOff ? " scax-set-notify__list--off" : ""}`}>{group.note}</p> : null}
          </section>
        );
      })}
    </>
  );
}
