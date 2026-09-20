# 로컬 업무 화면 데모

`make reset-demo`는 안전한 로컬 데모 DB를 초기화하고 조직·계정과 업무 12건을 만든다.
기존 데이터는 삭제된다. 별도 DB로 확인하려면 다음처럼 실행한다.

```sh
make reset-demo DATABASE_URL=sqlite:////tmp/ax_demo_my_work.db
```

기존 데모 계정 민아(`mina`, 비밀번호 `scax-demo-1234`)로 로그인한다.
이메일은 `AX_DEMO_EMAIL_DOMAIN` 설정을 따르는 기존 데모 계정 선택 화면에서 확인할 수 있다.

| 확인할 화면 | 예시 |
| --- | --- |
| 내 업무 / 진행 중 | 제품 사용성 조사 정리 |
| 막힘 | 연동 규격 확인 — 규격 회신 대기 사유 포함 |
| 기한 지남 | 지난주 고객 의견 분류 — 초기화 날짜보다 이틀 전 마감 |
| 캘린더 | 오늘 데모 점검 — 초기화 날짜에 시작·마감 |
| 완료 업무 | 제품 안내 문구 정리 |
| 받은 요청 / 수락 대기 | 다음 배포 안내 검토 요청 — 수락 전이므로 내 업무에는 없음 |
| 참조 수신함 | 제품 운영 점검 참조(대기), 분기 계획 검토 참조(수락됨) |
| 보낸 업무 | 제품 지표 검토 요청, 지표 정의 결과 확인 |
| 완료 보고 승인 대기 | 사용성 조사 결과 보고 — 지호가 판단 |
| 보완 요청 | 배포 체크리스트 보완 — 복구 절차 추가 사유 포함 |
| 민아의 판단함 | 지표 정의 결과 확인 — 승인·보완 요청 가능 |

날짜는 초기화 시 UTC 날짜 기준이다. 날짜가 지나면 다시 `make reset-demo`로 재생성한다.
캘린더는 별도 API 없이 `/api/my-work`와 `/api/tasks?include_closed=true`를 사용한다.
받은 요청은 `/api/work-requests/inbox`에서 `category=work`, CC 참조는 `category=reference`로 구분한다. CC는 처리 후에도 상태·판단 이력을 읽으며 수락·거절할 수 없다. TaskReference는 수신함에 포함하지 않는다.
보낸 요청은 `/api/work-requests`, 판단함은 `/api/action-items`로 확인한다.

업무 fixture는 reset CLI에서만 실행한다. 공용 `reset_database`/`seed_catalog`,
`--catalog-only`, `--sync`, 앱 시작 및 dataset import에는 추가하지 않는다.
생성과 상태 변경은 기존 application 명령으로 실행하며 실패 시 reset 명령도 실패한다.
OQ-203의 미완결 하위 업무 정책과 OQ-206의 승격 승인자 정책은 정하지 않는다.
승격 요청은 만들지 않고, 실제 요청자가 명확한 일반 요청만 완료 보고·보완 흐름에 사용한다.

좁은 API 검증(빈 DB → 초기화 → 데모 로그인 → 조회 → 재초기화):

```sh
PYTEST_ADDOPTS='-k "demo_work_seed or reset_demo"' make test-contract
```
