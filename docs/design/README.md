# 디자인 참조

`design-system-v2.dc.html`은 SCAX 상용 시스템 UI의 시각 grammar 기준선인 디자인 시스템 v2 원본이다. Claude Design canvas 문서(`.dc.html`)이며 브라우저에서 직접 열어 token·component·responsive 규칙을 확인한다. 문서 안의 `./support.js` 참조는 canvas editor 보조 스크립트로, 없어도 내용 열람에는 지장이 없다.

| 항목 | 값 |
|---|---|
| 원본 파일명 | `디자인 시스템 v2.dc.html` |
| SHA-256 | `02f7eb28baabbd01f5415dfd35e8140e8c40db40d400fa730d328aa4ce2fc246` |
| 편입일 | 2026-09-03 |

이 파일은 참조본이다. 실제 token·component 구현은 `frontend/src/styles.css`가 소유하며, 디자인 시스템 변경이 domain/application contract를 흔들지 않도록 frontend 경계 안에서만 반영한다. 정보 구조와 사용 흐름의 비교 기준은 `action-runtime`의 `dist-artifact/thesc.html`이며 이 저장소에 복제하지 않는다.
