import { DataTable, Td, Th } from "../../ds/DataTable";
import { meetingScreen } from "../../lib/labels";
import type { MeetingTermCorrection } from "../../lib/viewModels";

/**
 * 최종 회의록 끝의 「용어 보정」 표(SPEC-010 §2.5 · D-18) — **읽기 전용**(이번 판).
 *
 * 줄 순서는 서버가 준 대로(원문에 처음 나온 순 — OQ-1012) — 화면이 다시 정렬하지 않는다.
 * 비었을 때를 가른다(H-3): `[]`(정정이 돌았고 바꿀 것이 없었다) = 「바로잡은 용어 없음」 한 줄 ·
 * `null`(돌지 않았다)은 부르는 쪽이 이 부품을 아예 그리지 않는다.
 * 「바꿈」(`auto`)은 되돌릴 수 있는 쌍이다 — 들린 말이 이 표에 남아 사람이 [수정]에서 되돌린다(D-16).
 */
export function TermCorrections({ rows }: { rows: MeetingTermCorrection[] }) {
  return (
    <section aria-label={meetingScreen.termCorrectionsTitle} className="scax-agenda-block meeting-term-corrections">
      <div className="scax-agenda-block__head">
        <h3 className="scax-agenda-block__title">{meetingScreen.termCorrectionsTitle}</h3>
      </div>
      {rows.length === 0 ? (
        <p className="scax-agenda-block__source">{meetingScreen.termCorrectionsEmpty}</p>
      ) : (
        <DataTable className="meeting-term-table" label={meetingScreen.termCorrectionsTitle}>
          <thead>
            <tr>
              <Th>{meetingScreen.termCorrectionsHeard}</Th>
              <Th>{meetingScreen.termCorrectionsCorrected}</Th>
              <Th>{meetingScreen.termCorrectionsGrade}</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.heard}-${index}`}>
                <Td>{row.heard}</Td>
                <Td>{row.corrected}</Td>
                {/* 모르는 등급이 오면 지어내지 않고 서버 값을 그대로 낸다 */}
                <Td>{meetingScreen.termCorrectionGrade[row.grade] ?? row.grade}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
    </section>
  );
}
