# 백엔드 가동 안내

작성일: 2026-09-06. 대상: 운영자 1명.

코드는 전부 준비되어 있다. **여기 적힌 계정 만들기와 키 등록만 하면 가동된다.** 다른 코드 작업은 남아 있지 않다.

전체 소요 시간은 30~40분이고, 월 비용은 0원이다. 근거는 [백엔드 계획 16절](../KHU_BACKEND_PLAN.md)에 있다.

---

## 0. 미리 알아 둘 것

만들 계정은 셋이다. 셋 다 무료 요금제로 시작한다.

| 서비스 | 쓰는 이유 | 무료 한도 |
|---|---|---|
| Supabase | PostgreSQL 데이터베이스 | 500MB, 전송 5GB/월 |
| Cloudflare | R2 파일 저장 + Worker 조회 제공 | 저장 10GB, Worker 하루 10만 요청 |
| GitHub | 매시간 수집 실행 | 공개 저장소는 실행 시간 무제한 |

GitHub 계정은 이미 있다. 저장소는 `sihort183-creator/khu` 이고 공개 저장소다.

---

## 1. Supabase 프로젝트 만들기 (약 10분)

1. <https://supabase.com> 에서 GitHub 계정으로 가입한다.
2. **New project** 를 누른다.
   - Name: `khu-notice`
   - Database Password: 강한 문구를 만들어 **따로 안전하게 보관한다.** 이 값은 다시 볼 수 없다.
   - Region: **Northeast Asia (Seoul)** 을 고른다.
   - Plan: Free
3. 프로젝트 생성이 끝나면(2~3분) **Project Settings → Database → Connection string** 으로 간다.
4. **Session pooler** 탭을 고른다. Transaction pooler 가 아니다. 스키마 변경과 준비된 질의가 세션 방식에서만 안전하다.
5. 나오는 문자열을 복사한다. 이런 모양이다.

   ```
   postgresql://postgres.abcdefghijklm:[YOUR-PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres
   ```

6. `[YOUR-PASSWORD]` 를 2번에서 정한 실제 문구로 바꾼다. 이 완성된 문자열이 **`KHU_DATABASE_URL`** 이다.

> 포트가 `5432` 인지 확인한다. `6543` 이면 Transaction pooler 이므로 탭을 잘못 고른 것이다.

---

## 2. Cloudflare R2 만들기 (약 10분)

1. <https://dash.cloudflare.com> 에서 가입한다.
2. 왼쪽 메뉴에서 **R2 Object Storage** 를 누른다. 결제 수단 등록을 요구하면 등록한다. 무료 한도 안에서는 청구되지 않는다.
3. 버킷 3개를 만든다. 이름은 그대로 쓴다.
   - `khu-notice-public` — 조회용 JSON
   - `khu-notice-evidence` — 원문 증거
   - `khu-notice-backup` — 백업
   세 버킷 모두 **공개 접근을 켜지 않는다.** 조회는 Worker 를 통해서만 나간다.
4. R2 화면 오른쪽의 **Manage API tokens → Create API token** 을 누른다.
   - Token name: `khu-collector`
   - Permissions: **Object Read & Write**
   - Specify buckets: `khu-notice-public` 과 `khu-notice-evidence` 만 고른다.
   - **Create API Token** 을 누른다.
5. 나오는 값 셋을 복사한다. 이 화면을 닫으면 다시 볼 수 없다.
   - Access Key ID → **`KHU_R2_ACCESS_KEY_ID`**
   - Secret Access Key → **`KHU_R2_SECRET_ACCESS_KEY`**
   - 화면 위쪽 계정 ID(또는 endpoint 주소의 앞부분) → **`KHU_R2_ACCOUNT_ID`**
6. 백업용 토큰을 하나 더 만든다. 수집 실행의 키로 백업을 지울 수 없어야 한다(계획 17.2절).
   - Token name: `khu-backup`
   - Permissions: **Object Read & Write**
   - Specify buckets: `khu-notice-backup` 만 고른다.
   - 나오는 값은 **`KHU_R2_BACKUP_ACCESS_KEY_ID`**, **`KHU_R2_BACKUP_SECRET_ACCESS_KEY`** 다.

---

## 3. GitHub 비밀값 등록 (약 5분)

저장소 → **Settings → Secrets and variables → Actions → New repository secret** 에서 아래를 하나씩 넣는다.

| 이름 | 값 | 필수 |
|---|---|---|
| `KHU_DATABASE_URL` | 1단계에서 만든 연결 문자열 | 필수 |
| `KHU_R2_ACCOUNT_ID` | 2단계 계정 ID | 필수 |
| `KHU_R2_ACCESS_KEY_ID` | 2단계 `khu-collector` 키 | 필수 |
| `KHU_R2_SECRET_ACCESS_KEY` | 2단계 `khu-collector` 비밀 키 | 필수 |
| `KHU_R2_BACKUP_ACCESS_KEY_ID` | 2단계 `khu-backup` 키 | 백업용 |
| `KHU_R2_BACKUP_SECRET_ACCESS_KEY` | 2단계 `khu-backup` 비밀 키 | 백업용 |
| `KHU_BACKUP_PASSPHRASE` | 직접 정하는 긴 문구 | 권장 |
| `KHU_HEARTBEAT_URL` | 5단계 감시 주소 | 선택 |
| `KHU_BACKUP_HEARTBEAT_URL` | 5단계 감시 주소 | 선택 |

`KHU_BACKUP_PASSPHRASE` 는 백업 파일을 암호화한다. **이 문구를 잃어버리면 백업을 복원할 수 없다.** GitHub 비밀값 말고 개인 비밀번호 관리자에도 따로 보관한다.

---

## 4. 첫 가동 (약 10분)

저장소의 **Actions** 탭에서 순서대로 실행한다.

### 4-1. 스키마 만들기

1. **스키마 변경** 워크플로 → **Run workflow**
2. environment: `production`, dry_run: **체크 해제**, target: `head`
3. 실행한다. 36개 표와 검색 인덱스, 공개 조회 뷰가 만들어진다.

### 4-2. 출처 등록

1. **운영 명령** 워크플로 → **Run workflow**
2. command: `sources sync`
3. 실행한다. 조직 25개와 출처 78개가 들어간다. 모두 `pending` 상태로 시작한다.

### 4-3. 연락처 넣기

1. **운영 명령** → command: `contacts import`
2. 실행한다. 공식 전화번호 안내에서 만든 연락처 983건이 들어간다.

### 4-4. 출처가 실제로 열리는지 확인

1. **운영 명령** → command: `check-sources`
2. 실행 기록에서 `FAIL` 이 난 출처를 확인한다. 해외 접근이 막힌 출처는 `blocked_abroad` 로 표시된다.

### 4-5. 대표 출처 몇 개를 수집 대상으로 올리기

계획 5.2절대로 대표 표본부터 시작한다. **운영 명령** → command: `sources promote`, target 에 출처 이름 일부를 넣는다. 예를 들어 `소프트웨어융합학과 공지사항`.

서로 다른 게시판 구조가 섞이도록 10~15개를 고른다. 본부 1~2개, 단과대 2~3개, 학과 5~8개가 좋다.

### 4-6. 수집 실행

1. **공지 수집** 워크플로 → **Run workflow**
2. 실행이 끝나면 요약에 수집한 공지 수가 나온다.

이후로는 매시 17분에 자동으로 돈다.

---

## 5. Cloudflare Worker 배포 (약 5분)

조회 제공을 켜는 단계다. 이걸 해야 프론트가 데이터를 읽을 수 있다.

로컬에서 한 번만 실행한다.

```bash
cd worker && npx wrangler login && npx wrangler deploy
```

배포가 끝나면 `https://khu-notice-api.<계정이름>.workers.dev` 주소가 나온다. 브라우저로 `그 주소/v1/latest.json` 을 열어 개정 정보가 보이면 정상이다.

프론트에는 이 주소를 `NEXT_PUBLIC_API_BASE` 로 넣는다.

### 수집 깨우기 토큰 (권장)

GitHub 예약 실행은 2026-09-07 밤부터 여러 시간씩 빠졌다. 그래서 Worker 가 매시 17분에 GitHub 에
"수집 한 회차 시작" 요청을 대신 보낸다. 이 장치는 **토큰을 넣어야 켜진다.** 토큰이 없으면 Worker 는
아무 요청도 하지 않고 기록만 한 줄 남긴다(조회 기능은 토큰과 무관하게 그대로 동작한다).

**1) GitHub 에서 토큰 만들기** — 순서대로 누른다.

1. GitHub → 오른쪽 위 프로필 → **Settings**
2. 왼쪽 맨 아래 **Developer settings**
3. **Personal access tokens** → **Fine-grained tokens** → **Generate new token**
4. 이름은 아무거나(예: `khu-collect-waker`), 만료(Expiration)는 **1년**
5. **Repository access** → *Only select repositories* → **`sihort183-creator/khu` 하나만** 고른다
6. **Permissions** → *Repository permissions* → **Actions 를 `Read and write`** 로 바꾼다.
   **다른 권한은 하나도 주지 않는다**(Contents 도 주지 않는다)
7. **Generate token** → 화면에 뜬 값을 복사한다. 이 화면을 닫으면 값을 다시 볼 수 없다

**2) Cloudflare 에 비밀값으로 넣기** — 둘 중 하나만 하면 된다.

- 대시보드: Cloudflare → **Workers & Pages** → **`khu-notice-api`** → **Settings** →
  **Variables and Secrets** → **Add** → 종류를 **Secret** 으로 두고, 이름은 정확히
  **`KHU_GITHUB_TOKEN`**, 값에는 복사한 토큰을 붙여 넣고 저장한다.
- 또는 로컬에 Cloudflare 로그인이 되어 있으면:

  ```bash
  cd worker && npx wrangler secret put KHU_GITHUB_TOKEN
  ```

  물어보면 토큰 값을 붙여 넣는다.

비밀값은 Cloudflare 에 저장되므로 배포와 무관하다. 다시 배포해도 지워지지 않고, 저장소 파일 어디에도
값이 남지 않는다.

**3) 켜졌는지 확인** — 다음 매시 17분을 기다린 뒤 둘 중 아무거나 본다.

- Cloudflare → `khu-notice-api` → **Observability**(Worker 로그)에 `수집 깨우기: 회차 시작 …` 줄이 보인다.
  이미 도는 회차가 있던 시각이면 `이미 in_progress 회차가 있어 시작하지 않습니다` 가 보인다(정상이다).
- GitHub → 저장소 **Actions** → *공지 수집* 에 `workflow_dispatch` 로 시작된 회차가 뜬다.

**안전에 관해.** 이 토큰으로 할 수 있는 일은 **`sihort183-creator/khu` 저장소의 워크플로를 실행·조회하는
것뿐**이다. 코드를 읽거나 바꾸거나, 비밀값을 보거나, 다른 저장소를 건드릴 수 없다. 값이 새어 나갔다고
생각되면 GitHub → Settings → Developer settings → Fine-grained tokens 에서 그 토큰을 **Delete** 하면
바로 무효가 된다. 새로 만들어 같은 이름으로 다시 넣으면 된다.

### 감시 붙이기 (선택, 무료)

<https://healthchecks.io> 에서 무료 가입하고 확인 항목 2개를 만든다.

- `khu-collect` — 주기 1시간, 유예 2시간 → 주소를 `KHU_HEARTBEAT_URL` 로 등록
- `khu-backup` — 주기 6시간, 유예 1시간 → 주소를 `KHU_BACKUP_HEARTBEAT_URL` 로 등록

수집이나 백업이 멈추면 메일이 온다. 백업은 무료 요금제의 유일한 복구 수단이므로 이 감시를 붙이는 것을 권한다.

---

## 6. 로컬에서 돌려 보기

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env      # 값을 채운다
.venv/Scripts/python.exe -m pytest -q
```

검사는 네트워크와 운영 비밀값 없이 돈다. 실제 원문 표본으로 확인한다.

수집을 로컬에서 시험하려면 `.env` 에 `KHU_DATABASE_URL` 만 넣고 R2 값을 비워 둔다. 파일 저장이 `backend/.localstore/` 로 떨어진다.

---

## 7. 자주 쓰는 운영 명령

**운영 명령** 워크플로에서 고른다. 모든 변경은 `audit_logs` 에 사유와 실행자가 남는다.

| 하고 싶은 일 | command | target |
|---|---|---|
| 새 출처를 등록부에서 가져오기 | `sources sync` | — |
| 출처와 상태 보기 | `sources list` | — |
| 출처를 수집 대상으로 올리기 | `sources promote` | 출처 이름 일부 |
| 문제 있는 출처 멈추기 | `sources pause` | 출처 이름 일부 |
| 잘못된 공지 숨기기 | `notice hide` | 공지 식별자 |
| 정적 파일 다시 만들기 | `export` | — |
| 지금 상태 요약 | `status` | — |

공지를 숨기면 목록·상세·검색 색인에서 함께 사라진다. 반영은 다음 수집 실행 때이고, 급하면 `export` 를 바로 실행한다.

---

## 8. 새 출처를 늘리려면

```bash
python scripts/discover_sources.py --out registry/bootstrap/discovered.json
python scripts/build_registry.py
```

두 명령이 `registry/bootstrap/` 의 등록부를 다시 만든다. 바뀐 내용을 커밋하고 `sources sync` 를 실행하면 새 출처가 들어간다.

조직 이름을 찾지 못한 곳은 `[확인 필요]` 로 표시된다. 지금 8곳이 그렇다. 공식 이름을 확인해서 `registry/bootstrap/organizations.json` 을 직접 고치면 된다.

---

## 9. 학교 지원을 받으면

순서는 이렇다. 코드는 바꾸지 않는다.

1. **Supabase Pro** (월 25달러) — 자동 백업 7일, 디스크 8GB, 프로젝트 정지 없음
2. **도메인** (연 1.5~2만 원) — Cloudflare DNS 에 붙이고 Worker 에 연결
3. **소형 서버** (월 7~12달러) — 수집 주기를 1시간에서 15분으로 줄일 때만

무료 한도의 70%에 닿으면 **운영 명령 → status** 가 알려 준다. 그 전에는 아무것도 바꿀 필요가 없다.

---

## 문제가 생기면

| 증상 | 원인과 조치 |
|---|---|
| 수집 실행이 `KHU_DATABASE_URL 이 없습니다` 로 실패 | 3단계 비밀값 이름을 다시 확인한다 |
| `스키마 버전 불일치` | 4-1 단계 스키마 변경을 실행한다 |
| 데이터베이스 연결 시간 초과 | 연결 문자열 포트가 `5432` 인지 확인한다 |
| 특정 출처만 계속 실패 | `check-sources` 로 확인하고 필요하면 `sources pause` |
| 수집이 멈췄는데 몰랐다 | 5단계 감시를 붙인다. 저장소에 60일간 커밋이 없으면 예약 실행이 꺼지는데, 유지 워크플로가 3주마다 자동으로 막아 준다 |
| 백업 확인 실패 | `KHU_BACKUP_PASSPHRASE` 가 백업을 만들 때와 같은지 확인한다 |
