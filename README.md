# NEIS School Alert

NEIS Open API 기반으로 학교 시간표, 급식, 학사일정, 학교/학급 정보를 조회하고 웹과 텔레그램으로 알림을 제공하는 FastAPI 서비스입니다. Synology NAS에서 Docker로 배포할 수 있게 구성했습니다.

## 1. 전체 폴더 구조

```text
school_pjt/
├─ app/
│  ├─ main.py
│  ├─ config.py
│  ├─ db.py
│  ├─ models.py
│  ├─ schemas.py
│  ├─ utils.py
│  ├─ routes/
│  │  ├─ web.py
│  │  ├─ api.py
│  │  └─ telegram.py
│  ├─ services/
│  │  ├─ neis_client.py
│  │  ├─ timetable_service.py
│  │  ├─ meal_service.py
│  │  ├─ schedule_service.py
│  │  ├─ telegram_service.py
│  │  ├─ notification_service.py
│  │  ├─ profile_service.py
│  │  └─ region_service.py
│  ├─ static/
│  │  └─ style.css
│  └─ templates/
│     ├─ base.html
│     ├─ home.html
│     ├─ profiles.html
│     ├─ profile_form.html
│     ├─ profile_detail.html
│     ├─ timetable.html
│     ├─ meal.html
│     ├─ schedule.html
│     ├─ regions.html
│     ├─ region_detail.html
│     └─ admin_logs.html
├─ data/
├─ tests/
│  └─ test_services.py
├─ .env.example
├─ Dockerfile
├─ docker-compose.yml
├─ requirements.txt
└─ README.md
```

## 2. 핵심 설계 설명

- `NeisClient`는 NEIS Open API를 직접 호출하며 재시도, 응답 캐싱, JSON row 파싱을 담당합니다.
- `TimetableService`, `MealService`, `ScheduleService`는 학교급별 시간표 엔드포인트 선택, 알레르기 파싱, 학사일정 뱃지 분류를 담당합니다.
- `NotificationService`는 오늘/내일 브리핑, 준비물 자동 생성, 시간표 변경 감지, D-Day 알림, 중복 발송 방지를 담당합니다.
- 웹은 Jinja2 템플릿 기반 모바일 우선 UI이며, 브라우저 방문 시 생성되는 `web_key` 쿠키로 익명 사용자를 식별합니다.
- 텔레그램은 webhook 방식이며 `/register`, `/profiles`, `/today`, `/tomorrow`, `/meal`, `/schedule`, `/settings` 플로우를 제공합니다.
- APScheduler는 `Asia/Seoul` 기준으로 사전 동기화, 브리핑 발송, 변경 감지, 로그 정리를 수행합니다.

## 3. 각 파일의 전체 코드

전체 코드는 작업 디렉터리의 실제 파일에 들어 있습니다. 주요 진입점은 [app/main.py](/d:/pythonpjt/school_pjt/app/main.py), 설정은 [app/config.py](/d:/pythonpjt/school_pjt/app/config.py), NEIS 연동은 [app/services/neis_client.py](/d:/pythonpjt/school_pjt/app/services/neis_client.py)입니다.

## 4. 실행 방법

### SSH 접속 후 실행

```bash
ssh dalbong@stock.dalbong2.synology.me
```

프로젝트 폴더로 이동합니다.

```bash
cd /volume1/docker/school_pjt
```

환경변수 파일을 준비합니다.

```bash
cp .env.example .env
vi .env
```

필수 값:

- `NEIS_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_BOT_USERNAME`
- `APP_BASE_URL`
- `WEBHOOK_SECRET`
- `ADMIN_TOKEN`

컨테이너를 빌드하고 실행합니다.

```bash
docker compose up -d --build
```

실행 상태를 확인합니다.

```bash
docker compose ps
docker compose logs -f web
```

헬스체크를 확인합니다.

```bash
curl http://localhost:8000/health
```

정상 응답 예시:

```json
{"status":"ok"}
```

중지 또는 재시작:

```bash
docker compose down
docker compose restart web
```

코드 수정 후 재배포:

```bash
docker compose up -d --build
```

접속 주소 예시:

- 웹: `http://localhost:8000`
- 헬스체크: `http://localhost:8000/health`

## 5. .env.example

[.env.example](/d:/pythonpjt/school_pjt/.env.example) 파일을 사용합니다.

필수 환경변수:

- `NEIS_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `APP_BASE_URL`
- `APP_SECRET`
- `TZ=Asia/Seoul`

## 6. Dockerfile / docker-compose.yml

- Dockerfile: [Dockerfile](/d:/pythonpjt/school_pjt/Dockerfile)
- Compose: [docker-compose.yml](/d:/pythonpjt/school_pjt/docker-compose.yml)

SQLite는 `./data` 볼륨에 저장됩니다.

## 7. README 운영 가이드

### Synology NAS 배포 팁

- Synology Container Manager에서 프로젝트 폴더를 업로드하거나 Git 저장소를 클론합니다.
- `data` 폴더를 NAS 볼륨에 매핑해 SQLite 파일이 컨테이너 재생성 후에도 유지되게 합니다.
- NAS 역방향 프록시에서 `APP_BASE_URL` 도메인을 FastAPI 컨테이너로 전달합니다.
- 텔레그램 webhook을 사용할 경우 외부 HTTPS 주소가 필요합니다.
- SSH에서 직접 운영할 경우 프로젝트 경로를 예를 들어 `/volume1/docker/school_pjt`처럼 고정해 두는 편이 관리하기 쉽습니다.
- Synology DSM 방화벽이나 공유기 포트포워딩을 사용한다면 `8000` 포트를 직접 열기보다 역방향 프록시를 권장합니다.
- 학사일정은 기본적으로 연도 단위로 불러오고, NEIS 응답은 SQLite `neis_cache` 테이블에 저장됩니다.
- 앱 내부 APScheduler가 매일 새벽 `03:30`에 장기 캐시를 미리 덥혀 두도록 설정되어 있습니다.

### NAS 작업 스케줄러 권장 명령

Synology 작업 스케줄러에서 예비 작업으로 아래 명령을 등록해 두면, 컨테이너 내부에서 연간 학사일정과 자주 쓰는 데이터 캐시를 강제로 갱신할 수 있습니다.

```bash
cd /volume1/docker/school_pjt && docker compose exec -T web python -m app.jobs.prewarm_cache
```

권장 시간:

- 매일 `03:40`

효과:

- 학사일정은 해당 연도 1년치 기준으로 SQLite에 저장
- 학교기본정보/학급정보도 NAS DB 캐시에 저장
- 급식은 향후 2주치, 시간표는 최근 1주 범위를 미리 캐시
- 낮 시간대 첫 화면 로딩 시 NEIS 실시간 호출 부담 감소

### Telegram webhook 설정 방법

컨테이너 실행 후 다음 명령으로 webhook을 등록할 수 있습니다.

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://your-domain.example.com/telegram/webhook","secret_token":"telegram-webhook-secret"}'
```

### NEIS API 키 설정 방법

- NEIS Open API 포털에서 인증키를 발급합니다.
- `.env`의 `NEIS_API_KEY`에 입력합니다.
- 학교기본정보, 학급정보, 급식식단정보, 학사일정, 초/중/고/특수학교 시간표 데이터셋이 활성화된 키를 사용합니다.

### 장애 대응 방법

- `/admin/logs?token=<ADMIN_TOKEN>` 에서 최근 동기화와 발송 로그를 확인합니다.
- `notification_logs`에 `failed`가 쌓이면 `TELEGRAM_BOT_TOKEN`, webhook 공개 URL, NAS 프록시를 점검합니다.
- NEIS API 응답 실패가 반복되면 `sync_logs`와 컨테이너 로그를 함께 확인합니다.
- 캐시 이상 시 컨테이너 내 `data/school_alert.db`를 백업 후 정리합니다.

## 8. 학교 그룹(지역) 대시보드 기능

신규 기능으로 `학교 그룹(지역) 대시보드`가 추가되었습니다.

- 여기서 `region`은 행정동 자동 인식 개념이 아니라, 사용자가 직접 구성하는 `학교 묶음 그룹`입니다.
- 예: 그룹명 `병점`을 만들고, `안화고`, `병점중`, `진안중`을 학교명 검색으로 직접 추가.

- 웹 페이지:
  - `GET /regions`: 지역 목록, 지역 생성
  - `GET /regions/{region_id}`: 지역 상세 대시보드
- API:
  - `GET /api/schools/search?q=학교명&region_id=선택`
  - `GET /api/regions`
  - `POST /api/regions`
  - `GET /api/regions/{region_id}`
  - `POST /api/regions/{region_id}/schools/auto-discover`
  - `POST /api/regions/{region_id}/schools`
  - `DELETE /api/regions/{region_id}/schools/{school_id}`
  - `GET /api/regions/{region_id}/overview?target_date=YYYY-MM-DD`
  - `GET /api/regions/{region_id}/meals?target_date=YYYY-MM-DD`
  - `GET /api/regions/{region_id}/schedules?from=YYYY-MM-DD&to=YYYY-MM-DD`

### 사용 흐름

1. `/regions`에서 학교 그룹을 생성합니다.  
2. `/regions/{id}`에서 학교명을 검색합니다.  
3. 검색 결과(학교명/학교급/주소)를 확인하고 선택해 그룹에 추가합니다.  
4. 학사일정 중심 표에서 학교별 주요 일정을 한눈에 비교합니다.
5. 필요 시 그룹 상세에서 학교를 삭제한 뒤 같은 학교를 다시 재등록할 수 있습니다.

### 동작 특성

- 기존 `NeisClient`의 `schoolInfo`, `SchoolSchedule`, `mealServiceDietInfo` 호출을 그대로 재사용합니다.
- 지역 overview는 학교별 조회를 `asyncio.gather`로 병렬 처리합니다.
- 일부 학교 조회 실패 시 전체 실패 대신 partial success로 응답하며 `warnings[]`에 원인을 담습니다.
- 학사일정 파서는 아래 카테고리를 분류합니다.
  - 중간고사 / 기말고사 / 모의고사
  - 여름방학 / 겨울방학
  - 졸업식 / 개교기념일 / 재량휴업일
  - 종업식 / 시업식 / 입학식 / 방학식 / 기타행사
- `today_status`, `ongoing_events`, `upcoming_events(기본 14일)`를 계산해 학사일정 중심으로 보여줍니다.
- 급식은 보조 정보로 내려 `today_meal_summary`, `tomorrow_meal_summary`만 제공합니다.
- 모바일에서는 카드 뷰로 확인하기 쉽게 구성했습니다.

### 관리 작업

- 그룹 삭제:
  - 웹: `/regions` 목록에서 `그룹 삭제`
  - API: `DELETE /api/regions/{region_id}`
- 그룹 내 학교 삭제:
  - 웹: `/regions/{region_id}`의 학교 행에서 `삭제`
  - API: `DELETE /api/regions/{region_id}/schools/{school_id}`
- 학교 재등록:
  - 삭제된 학교(soft delete)는 같은 학교명 검색 후 다시 추가하면 자동 재활성화됩니다.
- 학교 상세 보기:
  - 그룹 상세의 `상세` 버튼으로 학교 상세 화면(`/schools/{atpt_code}/{school_code}`)에 진입합니다.
  - 학교명/학교급/교육청/주소/홈페이지/오늘 상태/오늘·내일 급식/오늘·이번주 학사일정을 확인할 수 있습니다.

### 향후 확장

- 현재 구조는 이후 텔레그램 `/region` 브리핑(학교 그룹별 시험/방학/행사 요약 전송)으로 확장하기 쉽게 설계되어 있습니다.

## 9. 추후 개선 포인트

1. 웹 로그인과 보호자 계정 권한 모델을 추가해 브라우저별 임시 쿠키 방식을 대체할 수 있습니다.
2. 프로필 수정 UI와 관리자 재시도 버튼을 별도 폼으로 확장할 수 있습니다.
3. 공휴일 API 연동을 추가하면 주말 외 공휴일 판별 정확도를 높일 수 있습니다.
4. SQLite 대신 PostgreSQL과 Redis를 붙이면 다중 인스턴스 운영이 쉬워집니다.
