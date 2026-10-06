import { useState, type FormEvent } from "react";

import { Button } from "../../ds/Button";
import { DropZone } from "../../ds/DropZone";
import { ApiError, changePassword, deleteProfileImage, uploadProfileImage } from "../../lib/api";
import { settingsScreen as copy } from "../../lib/labels";
import type { OrganizationProfile } from "../../lib/viewModels";
import { AssistantCharacter } from "../assistant/AssistantCharacter";
import { assistantCharacterCatalog, resolveAssistantCharacter } from "../assistant/assistantCharacterAssets";
import type { AssistantPresentationState } from "../assistant/assistantPresentation";
import { Card, Field } from "./settingsParts";

/**
 * 설정 — 프로필 설정 (SPEC-008 §2.6 · §4.7 · DC-4 · D-36·D-48).
 *
 * 머리: 이름·소속·직책·직무 — 명부 값이라 **읽기 전용**(입력칸 없음). 구획 셋:
 * - 프로필 이미지 — 고르면 **바로** 올라가 저장(「프로필 저장」 없음) · 1MB 이하 PNG·JPG · 삭제 · 없으면 이니셜
 * - AX 캐릭터 — 앱의 「내 AX 캐릭터」 고르기를 **이 자리로 옮겼다**(기존 모달 진입점 둘은 없앴다 · W-14). 저장은 기존
 *   `PUT …/assistant-character` 그대로이고, 저장·되돌림은 셸(App)이 쥔다(런처와 같은 값을 보므로)
 * - 비밀번호 변경 — 현재 틀림 401 · 규칙 위반 422 · 불일치는 화면에서
 */

const IMAGE_LIMIT = 1024 * 1024;
const IMAGE_TYPES = ["image/png", "image/jpeg"];
const previewState: AssistantPresentationState = { kind: "idle", label: "미리 보기", prompt: "" };

const fmtMB = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)}MB`;

function ProfileImageCard({ name, imageUrl, onChange }: { name: string; imageUrl: string | null; onChange: (url: string | null) => void }) {
  const [uploading, setUploading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const take = async (files: File[]) => {
    const file = files[0];
    if (!file) return;
    if (!IMAGE_TYPES.includes(file.type)) {
      setError(copy.imageWrongType(file.name));
      return;
    }
    if (file.size > IMAGE_LIMIT) {
      setError(copy.imageTooBig(file.name, fmtMB(file.size)));
      return;
    }
    setError(null);
    setUploading(file.name);
    try {
      const saved = await uploadProfileImage(file);
      onChange(saved.profile_image_url);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 413) setError(copy.imageTooBig(file.name, fmtMB(file.size)));
      else if (reason instanceof ApiError && reason.status === 415) setError(copy.imageWrongType(file.name));
      else setError(reason instanceof Error && reason.message ? reason.message : copy.imageFailed);
    } finally {
      setUploading(null);
    }
  };
  const remove = async () => {
    setUploading("");
    try {
      await deleteProfileImage();
      onChange(null);
    } catch (reason) {
      setError(reason instanceof Error && reason.message ? reason.message : copy.imageFailed);
    } finally {
      setUploading(null);
    }
  };
  const busy = uploading !== null;
  return (
    <Card title={copy.image}>
      <div className="scax-set-avatar">
        <span className={`scax-set-avatar__frame${busy ? " scax-set-avatar__frame--busy" : ""}`}>
          {imageUrl ? (
            <img alt={copy.imageCurrent} className="scax-set-avatar__img" src={imageUrl} />
          ) : (
            <span aria-label={copy.imageNone} className="scax-set-avatar__initial" role="img">
              {name.slice(0, 1)}
            </span>
          )}
          {busy ? <span className="scax-set-avatar__busy">{copy.imageUploading}</span> : null}
        </span>
        <div className="scax-set-avatar__side">
          <DropZone accept="image/png,image/jpeg" disabled={busy} drop={copy.imageDrop} hint={copy.imageHint} onFiles={(files) => void take(files)} pickLabel={copy.imagePick} />
          {error ? (
            <p className="scax-set-error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="scax-set-avatar__row">
            <span className="scax-set-row__meta">{imageUrl ? copy.imageHas : copy.imageEmpty}</span>
            {imageUrl ? <Button disabled={busy} label={copy.imageDelete} onClick={() => void remove()} size="sm" tone="neutral" variant="text" /> : null}
          </div>
        </div>
      </div>
    </Card>
  );
}

/** 앱의 「내 AX 캐릭터」 고르기를 옮긴 자리 — 후보 · 이름 · 「사용 가능」 · 고르면 바로 저장 · 실패하면 이전 값으로. */
export function CharacterCard({ currentKey, busy, error, onSelect }: { currentKey: string; busy: boolean; error: string | null; onSelect: (key: string) => void }) {
  const resolved = resolveAssistantCharacter(currentKey);
  const unsupported = resolved.key !== currentKey;
  return (
    <Card legend={busy ? copy.characterSaving : null} note={copy.characterNote} title={copy.character}>
      {unsupported ? (
        <p className="scax-character-picker__notice" role="status">
          {copy.characterUnsupported}
        </p>
      ) : null}
      {error ? (
        <p className="scax-set-error" role="alert">
          {error}
        </p>
      ) : null}
      <div aria-label={copy.characterList} className="scax-set-chars" role="radiogroup">
        {assistantCharacterCatalog.map((asset) => (
          <button
            aria-checked={resolved.key === asset.key}
            aria-label={copy.characterPick(asset.name)}
            className="scax-set-char"
            disabled={busy}
            key={asset.key}
            onClick={() => {
              if (asset.key !== currentKey) onSelect(asset.key);
            }}
            role="radio"
            type="button"
          >
            <AssistantCharacter characterKey={asset.key} size="header" state={previewState} />
            <b>{asset.name}</b>
            <small>{asset.productionApproved ? copy.characterReady : copy.characterPoster}</small>
          </button>
        ))}
      </div>
    </Card>
  );
}

function PasswordCard({ onNotice }: { onNotice: (message: string) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [wrong, setWrong] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);
  const mismatch = confirm !== "" && next !== confirm;
  const canSubmit = current !== "" && next !== "" && confirm !== "" && !mismatch && !busy;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setWrong(false);
    setRejected(null);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      onNotice(copy.passwordDone);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) setWrong(true);
      /* 422 `password_rejected` — 서버 사유는 코드라 우리 문구로 낸다 */ else if (reason instanceof ApiError && reason.status === 422) setRejected(copy.passwordRejected);
      else setRejected(reason instanceof Error && reason.message ? reason.message : copy.passwordFailed);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card note={copy.passwordNote} title={copy.password}>
      <form onSubmit={(event) => void submit(event)}>
        <div className="scax-set-form scax-set-form--row scax-set-form--pw">
          <Field label={copy.passwordCurrent}>
            <input
              aria-label={copy.passwordCurrent}
              autoComplete="current-password"
              className={`scax-set-input${wrong ? " scax-set-input--error" : ""}`}
              onChange={(event) => {
                setCurrent(event.target.value);
                setWrong(false);
              }}
              placeholder="••••••••"
              type="password"
              value={current}
            />
            {wrong ? <span className="scax-set-error scax-set-error--field">{copy.passwordWrong}</span> : null}
          </Field>
          <Field label={copy.passwordNew}>
            <input
              aria-label={copy.passwordNew}
              autoComplete="new-password"
              className={`scax-set-input${rejected ? " scax-set-input--error" : ""}`}
              onChange={(event) => {
                setNext(event.target.value);
                setRejected(null);
              }}
              placeholder="••••••••"
              type="password"
              value={next}
            />
            {rejected ? <span className="scax-set-error scax-set-error--field">{rejected}</span> : null}
          </Field>
          <Field label={copy.passwordConfirm}>
            <input
              aria-label={copy.passwordConfirm}
              autoComplete="new-password"
              className={`scax-set-input${mismatch ? " scax-set-input--error" : ""}`}
              onChange={(event) => setConfirm(event.target.value)}
              placeholder="••••••••"
              type="password"
              value={confirm}
            />
            {mismatch ? <span className="scax-set-error scax-set-error--field">{copy.passwordMismatch}</span> : null}
          </Field>
        </div>
        <div className="scax-set-foot">
          <Button disabled={!canSubmit} label={copy.passwordSubmit} tone="primary" type="submit" variant="solid" />
        </div>
      </form>
    </Card>
  );
}

export function ProfileSection({
  session,
  onProfileImage,
  characterBusy,
  characterError,
  onChooseCharacter,
  onNotice,
}: {
  session: OrganizationProfile;
  onProfileImage: (url: string | null) => void;
  characterBusy: boolean;
  characterError: string | null;
  onChooseCharacter: (key: string) => void;
  onNotice: (message: string) => void;
}) {
  const name = session.display_name;
  const imageUrl = session.profile_image_url ?? null;
  const unit = session.organizations.map((organization) => organization.name).join(" · ");
  return (
    <>
      <section aria-label={copy.me} className="scax-set-me">
        {imageUrl ? (
          <img alt="" className="scax-set-me__avatar" src={imageUrl} />
        ) : (
          <span aria-hidden className="scax-set-me__avatar scax-set-me__avatar--initial">
            {name.slice(0, 1)}
          </span>
        )}
        <dl className="scax-set-me__facts">
          <div>
            <dt>{copy.facts.name}</dt>
            <dd>{name}</dd>
          </div>
          <div>
            <dt>{copy.facts.unit}</dt>
            <dd>{unit || copy.never}</dd>
          </div>
          <div>
            <dt>{copy.facts.position}</dt>
            <dd>{session.position || copy.never}</dd>
          </div>
          <div>
            <dt>{copy.facts.duty}</dt>
            <dd>{session.job || copy.never}</dd>
          </div>
        </dl>
        <p className="scax-set-me__note">{copy.meNote}</p>
      </section>
      <ProfileImageCard imageUrl={imageUrl} name={name} onChange={onProfileImage} />
      <CharacterCard busy={characterBusy} currentKey={session.assistant_character?.character_key ?? "cream-cat"} error={characterError} onSelect={onChooseCharacter} />
      <PasswordCard onNotice={onNotice} />
    </>
  );
}
