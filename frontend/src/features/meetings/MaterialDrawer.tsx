import { useEffect, useState } from "react";

import { meetingMaterialContentUrl, readMeetingMaterialText } from "../../lib/api";
import { AssistantMarkdown } from "../chat/AssistantMarkdown";
import { Empty } from "../../ds/Empty";
import { Drawer } from "../../ds/Modal";
import { Skeleton } from "../../ds/Skeleton";
import { meetingClock, meetingScreen } from "../../lib/labels";
import type { MeetingMaterial } from "../../lib/viewModels";
import { sizeText } from "./AttachModal";

/** 데모가 받는 것은 PDF · Markdown 둘이다 (SPEC §10). */
function isMarkdown(material: MeetingMaterial): boolean {
  return material.content_type.includes("markdown") || /\.(md|markdown)$/i.test(material.name);
}

/**
 * `E64` 자료 미리보기 — **행을 누르면 드로어로 그 자료 하나만 연다** (X-142 개정).
 *
 * 탭 안에 미리보기를 깔지 않는다: 오른쪽 탭이 좁아 목록과 본문이 서로를 밀고, 자료는 「띄워 놓고
 * 이야기하는 것」이라 넓게 봐야 한다. PDF 는 브라우저가 그대로 띄우고 Markdown 은 글자로 읽는다.
 */
export function MaterialDrawer({
  meetingId,
  material,
  onClose,
}: {
  meetingId: string;
  material: MeetingMaterial | null;
  onClose: () => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const markdown = material !== null && isMarkdown(material);

  useEffect(() => {
    if (!material || !markdown) return;
    let cancelled = false;
    setText(null);
    setFailed(false);
    void readMeetingMaterialText(meetingId, material.material_id)
      .then((body) => {
        if (!cancelled) setText(body);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [markdown, material, meetingId]);

  if (!material) return null;
  const url = meetingMaterialContentUrl(meetingId, material.material_id);

  return (
    <Drawer
          closeLabel="상세 닫기"
      footer={
        <>
          <span className="t-meta tabular" style={{ fontSize: 12 }}>
            {[sizeText(material.size), meetingClock(material.uploaded_at)].join(" · ")}
          </span>
          <span className="scax-drawer__spacer" />
          {/* 받는 것은 브라우저가 한다 — 서버가 Content-Disposition 을 실어 보낸다 */}
          <a className="scax-button scax-button--outlined-neutral scax-button--lg" href={url}>
            {meetingScreen.download}
          </a>
        </>
      }
      kicker={markdown ? "Markdown 문서" : "PDF 문서"}
      /* §8-B 13: 시안의 자료 서랍은 520 이다 — 본문이 한 열(미리보기 글줄)뿐이라 840 은 과했다.
         변형은 바퀴 3b 가 이미 만들어 두었다 (`.scax-drawer--sm`) */
      size="sm"
      label="자료 보기"
      onClose={onClose}
      title={material.name}
    >
      {markdown ? (
        failed ? (
          <Empty title={meetingScreen.listError} variant="error" />
        ) : text === null ? (
          <Skeleton label="자료를 불러오는 중" />
        ) : (
          <AssistantMarkdown body={text} />
        )
      ) : (
        <object aria-label={material.name} className="material-frame" data={url} type={material.content_type || "application/pdf"}>
          <Empty title={meetingScreen.materialsEmpty} />
        </object>
      )}
    </Drawer>
  );
}
