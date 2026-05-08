# Discord Study Time Bot

Discord 음성 채널 체류 시간을 기록하고, Notion 데이터베이스 변경 사항과 공부 알림을 Discord 채널로 알려주는 개인용 스터디 관리 봇입니다.

## 주요 기능

- 특정 음성 채널 입장/퇴장 감지
- 사용자별 음성 채널 체류 시간 누적
- 매주 일요일 23:00(KST)에 주간 체류 시간 리포트 전송
- 30분 이상 음성 채널에 머문 경우 Notion 일정 DB에 공부 기록 생성
- Notion 기능 요청 DB, 게시판 DB 변경 감지 후 Discord 알림
- `!이름` 형식으로 서버 멤버를 빠르게 멘션하는 단축 기능
- `/menu`, `!menu` 명령어로 메뉴 랜덤 추천
- 매일 12:00(KST)에 랜덤 공부 알림 및 3일 이상 공부 기록이 없는 멤버 알림
- 봇 재시작 시 최신 Git 커밋 정보를 포함한 배포 완료 알림

## 사용 기술

- Python 3.10
- discord.py
- python-dotenv
- aiohttp
- notion-client
- pytz
- Docker / Docker Compose

## 프로젝트 구조

```text
.
├── main.py                  # 봇 실행 진입점
├── bot.py                   # Discord 봇 인스턴스, on_ready 이벤트, 배포 알림
├── config.py                # 환경 변수 로드 및 설정값 관리
├── cogs/
│   ├── voice_time.py        # 음성 채널 체류 시간 기록, 주간 리포트, Notion 공부 기록
│   ├── mention_shortcut.py  # 멘션 단축 기능
│   ├── menu_commands.py     # 메뉴 추천 명령어
│   ├── notion_watcher.py    # Notion DB 변경 감지
│   └── study_reminder.py    # 공부 리마인더
├── data/                    # 봇 상태와 메뉴 데이터 저장
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── redeploy.sh              # 서버 재배포 스크립트
```

## 시작하기

### 1. 저장소 클론

```bash
git clone <repository-url>
cd discord-bot-time
```

### 2. 가상환경 생성 및 활성화

macOS 기준:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

터미널 앞에 `(.venv)`가 표시되면 가상환경이 활성화된 상태입니다.

### 3. 의존성 설치

```bash
pip install -r requirements.txt
```

### 4. 환경 변수 설정

`.env.example` 파일을 복사해서 `.env` 파일을 만든 뒤, 필요한 값을 채워주세요.

```bash
cp .env.example .env
```

필수 값:

```env
DISCORD_TOKEN=
VOICE_CHANNEL_ID=
REPORT_CHANNEL_ID_ENTER=
```

선택 값:

```env
MENTION_CHANNEL_ID=
REPORT_CHANNEL_ID_FEATURE=
REPORT_CHANNEL_ID_DEPLOY=
REPORT_CHANNEL_ID_ALARM=
REPORT_CHANNEL_ID_DAILY=
REPORT_CHANNEL_ID_CHASE=
DATA_FILE=data/voice_time.json
NOTION_TOKEN=
NOTION_DATABASE_FEATURE_ID=
NOTION_DATABASE_BOARD_ID=
NOTION_DATABASE_SCHEDULE_ID=
DD_API_KEY=
```

`config.py`에서 `DISCORD_TOKEN`, `VOICE_CHANNEL_ID`, `REPORT_CHANNEL_ID_ENTER` 값이 없으면 봇 실행이 중단됩니다.

### 5. 로컬 실행

```bash
python3 main.py
```

봇이 정상적으로 실행되면 콘솔에 로그인한 봇 계정과 슬래시 명령어 동기화 로그가 출력됩니다.

## Docker로 실행하기

Docker Compose를 사용하면 서버에서 백그라운드로 실행할 수 있습니다.

```bash
docker compose up -d --build
```

로그 확인:

```bash
docker compose logs -f
```

중지:

```bash
docker compose down
```

`docker-compose.yml`은 `./data` 폴더를 컨테이너의 `/app/data`에 연결합니다. 따라서 컨테이너를 다시 만들어도 음성 시간 기록과 Notion 감지 상태가 유지됩니다.

## 재배포

서버에서 최신 코드를 받아 다시 빌드하려면 다음 스크립트를 사용할 수 있습니다.

```bash
chmod +x redeploy.sh
./redeploy.sh
```

스크립트는 다음 작업을 수행합니다.

1. `main` 브랜치 최신 코드 pull
2. `.env` 파일 존재 여부 확인
3. Docker 이미지 재빌드 및 컨테이너 재시작
4. 사용하지 않는 Docker 이미지 정리

## Discord 명령어

### 메뉴 추천

```text
/menu
!menu
```

`data/menus_kr.json`에 있는 메뉴 중 하나를 추천합니다. 최근 3일 안에 추천된 메뉴는 되도록 피합니다.

### 음성 시간 확인

```text
!voicetime
```

관리자 권한이 있는 사용자가 현재 누적된 음성 채널 체류 시간을 확인할 수 있습니다.

### 멘션 단축

```text
!닉네임
```

서버 멤버의 표시 이름, 사용자 이름, 글로벌 이름과 일치하는 사용자를 찾아 멘션합니다. `MENTION_CHANNEL_ID`가 설정되어 있으면 해당 채널로 메시지를 보냅니다.

## Notion 연동

Notion 연동을 사용하려면 `NOTION_TOKEN`과 필요한 데이터베이스 ID를 `.env`에 설정해야 합니다.

- `NOTION_DATABASE_FEATURE_ID`: 기능 요청/완료 알림 대상 DB
- `NOTION_DATABASE_BOARD_ID`: 게시판 새 글 알림 대상 DB
- `NOTION_DATABASE_SCHEDULE_ID`: 30분 이상 공부 기록을 생성할 일정 DB

Notion API 통합이 각 데이터베이스에 접근할 수 있도록 Notion에서 integration을 연결해야 합니다.

## 데이터 파일

- `data/voice_time.json`: 사용자별 음성 채널 세션, 주간 누적 시간, 마지막 공부 기록 저장
- `data/notion_db.json`: Notion DB에서 이미 감지한 row 상태 저장
- `data/menus_kr.json`: 메뉴 추천 후보 목록
- `data/menu_history.json`: 최근 추천 메뉴 기록

운영 중 생성되는 데이터 파일은 봇 상태를 유지하는 데 사용됩니다.

## 주의사항

- Discord Developer Portal에서 봇 토큰을 발급하고, 필요한 intent를 활성화해야 합니다.
- 이 봇은 멤버 목록과 메시지 내용을 사용하므로 `Server Members Intent`, `Message Content Intent`가 필요합니다.
- `.env`에는 토큰과 채널 ID가 들어가므로 GitHub에 커밋하지 않도록 주의해주세요.
- Notion 기능을 사용하지 않는 경우 Notion 관련 환경 변수는 비워둘 수 있습니다.
