## 변경 내용

<!-- 사용자에게 보이는 결과와 변경 이유를 간단히 적어 주세요. -->

## 테스트 책임

- [ ] 새 상태·값·권한·중복·projection 판단을 domain/policy가 소유하며 unit에서 검증했다. 또는 해당 판단 변경이 없다.
- [ ] Aggregate 변경은 `State + Command → Transition/Outcome + Event/Effect Request`, 교차 판단은 `Context → Decision`, 읽기는 `Context → Projection`, 유효 값은 Value Object로 표현했다. 또는 도메인 판단 변경이 없다.
- [ ] application/service에는 port 조회, authorization 재검사, lock·version·transaction 안의 domain 결과 적용만 남겼다. 또는 application/service 변경이 없다.
- [ ] 범용 `Facts/Plan/plan_*`을 추가하지 않았다. 실제 ordered/retry/compensation Execution Plan이라면 그 이유를 설명했다.
- [ ] contract는 새 공개 application·HTTP·MCP·CLI·adapter 경계의 대표 journey만 추가했다. 또는 contract를 추가하지 않았다.
- [ ] integration은 실제 PostgreSQL constraint·locking·transaction·dialect·repository mapping에만 추가했다. 또는 integration을 추가하지 않았다.
- [ ] 새 unit과 중복되는 기존 contract matrix를 확인해 대표 boundary만 남겼다. 또는 대체되는 contract가 없다.

### 계층별 변경 근거

<!-- 추가하거나 제거한 계층만 남기세요. 개수보다 어떤 실패를 잡는지가 중요합니다. -->

| 계층 | 추가/제거 | 이 계층이어야 하는 이유와 잡는 실패 |
|---|---:|---|
| Unit |  |  |
| Contract |  |  |
| PostgreSQL integration |  |  |

## 검증

<!-- 실행한 명령과 결과를 적고, 실행하지 못한 검증은 이유와 residual risk를 남겨 주세요. -->

- [ ] `make test`
- [ ] 변경 범위에 필요한 focused test
- [ ] DB 고유 동작 변경 시 `make test-postgres`
- [ ] `git diff --check`
