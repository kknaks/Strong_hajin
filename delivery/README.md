# SCAX 보호 이미지 빌드와 납품

이 경로는 SCAX backend 원본 Python 파일을 포함하지 않는 `linux/amd64` 이미지를 만든다. Community Nuitka 컴파일은 배포 파일의 소스 제거 수단이지 암호화나 역공학 방지 보장이 아니다.

## 고정된 빌드 입력

| 구성 | 납품 기준 |
|---|---|
| Python | 3.13.15 |
| Nuitka | Community 4.2.1 |
| Codex CLI | 0.153.4, 공식 npm package의 `linux-x64` native vendor만 복사 |
| compiler base | `python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e` |
| Codex extraction base | `node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e` |
| runtime base | `debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171` |

Python 3.13.15는 2026-09-11 기준 3.13 최신 maintenance release이고 3.12.14는 최신 security-only release다. 같은 revision·Nuitka·lockfile로 두 버전을 비교해 3.13을 선택했다. 패키지의 공개 최소 지원 범위인 `requires-python >=3.12`는 바꾸지 않는다.

| 후보 | dependency/compile | image scanner | runtime smoke | 판단 |
|---|---|---|---|---|
| 3.12.14 | 동일 lockfile 59개, Nuitka 4.2.1 성공 | `sha256:0889a122baf11ed2f0b766ff3443ba46c873dfdd4b0e39718c1bdc82275e0b5c`, 808,045,897 bytes, source/layer leak 0, `libpython3.12.so.1.0` | SQLite API·세 worker·MCP initialize/list/`task_list` 통과 | 호환 fallback; security-only |
| 3.13.15 | 동일 lockfile 59개, Nuitka 4.2.1 성공 | `sha256:57b3e6c64bc59329fd0a24256840085064bc6fccb000954370483ad04028dd7b`, 807,518,377 bytes, source/layer leak 0, `libpython3.13.so.1.0` | SQLite 및 별도 PostgreSQL 16.6에서 API·세 worker·MCP initialize/list/`task_list` 통과 | 납품 기본; bugfix 지원 단계 |

빌드 시간은 ARM host의 amd64 emulation과 cold C cache 영향을 받았으므로 성능 선정 근거로 사용하지 않는다.

## 빌드와 검사

Docker Buildx와 `linux/amd64` 실행 지원이 필요하다. ARM 개발 호스트의 QEMU 빌드는 검증용이며 느릴 수 있다.

```sh
make protected-build
make protected-inspect
docker image inspect scax-protected:test \
  --format '{{.Id}} {{.Config.Labels}}'
```

`protected-inspect`는 컨테이너의 `/opt`와 `docker save`로 내보낸 모든 최종 이미지 레이어를 검사한다. `.py/.pyc/.pyo`, 생성 C/header, `.git`, test/cache/virtualenv, `.env`, 개인 키와 인증 파일이 발견되면 실패한다. ELF 문자열에서는 synthetic canary, SCAX module 경로와 내부 심볼 잔류를 보고한다. 이미지 label과 포함된 `libpythonX.Y.so`가 다르면 Python ABI mismatch로 실패한다.

빌드는 회사가 통제하는 builder에서 수행하고 고객에게는 검증한 최종 image archive와 digest·운영 문서·필수 OSS 고지만 전달한다. Git 이력은 암호화하는 것이 아니라 `.dockerignore` 단계부터 build context에서 제외한다. compiler stage와 BuildKit cache에는 입력 Python과 Nuitka 생성 C가 존재하므로 source repository, build context, 중간 image, compiler output과 builder cache를 고객에게 전달하거나 고객 환경에서 생성하지 않는다. `docker save`의 최종 image archive에는 사용하지 않은 compiler stage가 포함되지 않으며 scanner가 그 archive의 실제 layer를 다시 확인한다.

Python 또는 base 보안 패치를 올릴 때에는 떠다니는 tag를 직접 배포하지 않는다. 새 공식 tag의 multi-platform index digest를 확인해 Dockerfile과 Makefile의 version/ref를 함께 바꾸고, 3.13 빌드·전체 scanner·API/워커/MCP/PostgreSQL smoke를 다시 수행한다. dependency와 Nuitka 버전도 lockfile 및 label과 함께 갱신한다.

## 실행 역할과 설정

하나의 `/opt/scax/scax` 실행 파일이 다음 역할을 dispatch한다.

```sh
docker run --rm --env-file scax.env -p 8000:8000 scax-protected:test api
docker run --rm --env-file scax.env scax-protected:test conversation-worker
docker run --rm --env-file scax.env scax-protected:test material-worker
docker run --rm --env-file scax.env scax-protected:test meeting-worker
docker run --rm --env-file scax.env scax-protected:test report-worker
docker run --rm -i --env-file scax.env scax-protected:test mcp
```

필수 application 설정은 기존 SCAX 계약과 같다. 대표적으로 `AX_PROFILE`, `DATABASE_URL`, worker queue 설정, MCP의 `AX_MCP_PERSONA`가 있다. Codex나 Soniox credential은 image/config layer에 넣지 않고 설치 환경의 secret 주입 수단으로 전달한다. 자료 원본이 필요하면 `AX_MATERIALS_DIR`가 가리키는 외부 volume을 non-root UID/GID 10001이 읽고 쓸 수 있게 mount한다. 기본 사용자는 `scax`, API bind는 `0.0.0.0:8000`이며 `SCAX_API_HOST`·`SCAX_API_PORT`로 바꾼다.

`AX_MATERIALS_DIR`와 `AX_RECORDINGS_DIR`는 운영에서 영속 volume으로 지정한다. 지정하지 않으면 기본 working directory 아래 `.scax/`를 사용하므로 container writable layer 용량 부족 시 시작에 실패하고 교체 시 데이터가 사라진다. `ENOSPC`가 보이면 image 문제가 아닌지 먼저 확인한 뒤, 정확한 volume mount와 host/container 용량을 점검한다. 공용 Docker cache/volume을 무차별 prune하지 않는다.

보호 이미지에서 Codex가 MCP를 다시 열 때는 `SCAX_RUNTIME_EXECUTABLE=/opt/scax/scax`를 사용한다. source 개발 경로의 `python -m ax_workspace.entrypoints.mcp`는 그대로 유지한다.

## 노출 결과와 보호 gate

Community 결과에서 원본 SCAX `.py`와 생성 C는 최종 filesystem과 최종 레이어에 없었다. 반면 synthetic canary, `ax_workspace/...py` module 경로와 내부 class/function/environment 이름은 ELF `strings`로 관찰됐다. 최적화, docstring 제거, symbol strip은 이 값을 암호화하지 않는다. 고객 관리자 권한이 있으면 실행 메모리, 프로세스 argument/environment, DB schema, API/MCP Tool schema와 네트워크 흐름도 관찰할 수 있다.

Nuitka Commercial의 `data-hiding`은 별도 유료 라이선스 gate다. 라이선스 구매나 약관 동의는 이 작업에 포함되지 않았고 현재 이미지에는 적용하지 않았다. 향후 정식 라이선스로 빌드할 때는 Community image tag와 구분하고 다음 검사를 통과해야만 상수 보호 적용으로 기록한다.

```sh
make protected-inspect \
  PROTECTED_IMAGE=scax-protected-commercial:<version> \
  PROTECTED_EXPECT_CONSTANTS=hidden
```

이 검사는 canary가 숨겨졌다는 좁은 증거일 뿐 역공학 불가능의 증거가 아니다. 실제 secret은 바이너리 상수로 포함하지 않는다.

## 내부 MCP와 선택적 외부 MCP

현재 납품 이미지가 구현하는 MCP는 SCAX 대화 워커가 실행하는 Codex CLI용 stdio 경로다. 온프레미스와 클라우드 모두 이 경로가 기본이며 변경하지 않는다.

고객 AI client용 외부 MCP는 선택 제공 방향만 승인됐고 기본 비활성이다. 향후 구현 시 Streamable HTTP 후보 endpoint에서 외부 인증을 내부 principal·scope로 해소하고, stdio와 같은 capability/resource 권한·사람 승인·idempotency·audit/receipt를 호출마다 다시 적용해야 한다. 노출 Tool은 surface별 allowlist로 제한한다. protocol/SDK, client 호환 범위, 인증, ingress/port, process 격리와 운영 책임은 미정이며 이번 이미지가 endpoint를 구현하거나 개방하지 않는다.

원본 소스를 제거해도 외부 MCP를 열면 허용한 Tool 이름·입출력 schema·오류와 운영 endpoint 계약은 client에 공개된다. 이는 binary 보호와 별개의 API surface 결정이다.

## 설치 환경 차이

같은 보호 image와 Python 버전을 쓰며 application·권한·데이터 계약은 환경별로 갈라지지 않는다.

| 항목 | 고객 온프레미스 | 고객 클라우드 | 배포 전 확인 |
|---|---|---|---|
| network/egress | 고객 방화벽·proxy·TLS 정책에 맞춤 | 고객 VPC/VNet·egress·TLS 정책에 맞춤 | Codex/Soniox 도달성; 완전 폐쇄망 지원 아님 |
| secret | 고객 승인 secret store 또는 runtime file/env | 고객 cloud secret service 또는 runtime file/env | image/layer/log에 값이 없는지 |
| DB·자료 | 고객 운영 PostgreSQL과 volume/object adapter | 고객이 선택한 PostgreSQL과 volume/object adapter | migration baseline, backup/restore, 암호화와 권한 |
| log/감사 | 고객 수집 agent·보존 정책 | 고객 logging service·보존 정책 | 개인정보 masking, receipt/causation 보존 |
| update | 고객 반입·검증·rollback 절차 | 고객 registry promotion·rollback 절차 | digest verification, 이전 image 보존 |
| sizing | 설치 서버의 CPU/RAM/disk 측정 | 선택 compute의 CPU/RAM/disk 측정 | 동시 사용자·자료량·worker backlog 부하 측정 |

최종 CPU architecture, 자원 sizing/SLA, cloud vendor/Kubernetes/managed service, OIDC와 schema migration은 아직 확정 또는 구현된 것으로 간주하지 않는다. 실제 고객 배포·registry push·운영 연결은 별도 승인과 검증이 필요하다.

## 런타임 구성과 진단

최종 image에는 Debian runtime package(`ca-certificates`, `libgomp1`, `tini`), Nuitka standalone이 수집한 CPython shared library·표준/제3자 native module·필수 package data, Codex native 실행 파일이 들어간다. Python package 해상도는 `backend/uv.lock`, 컴파일 시 생성한 license report는 `/usr/share/doc/scax/third-party-licenses.rst`에 있다. Debian과 Codex의 별도 license/notice 및 고객 납품용 OSS 고지는 release compliance 검토 대상이다.

장애 진단 순서는 image digest/label과 architecture 확인 → non-root volume 권한 → DB/TLS/egress 확인 → 역할별 process log 확인 → 같은 digest의 disposable smoke 재현이다. source traceback 대신 module path와 오류는 남을 수 있으며, 진단을 위해 실제 credential·고객 데이터 또는 바이너리 전체를 일반 로그에 남기지 않는다.
