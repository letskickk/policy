# AWS 서버 배포 가이드 (EC2 기준)

---

## 0. EC2 인스턴스 생성 (처음 한 번)

AWS 콘솔에서 서버(EC2)를 하나 만드는 방법입니다.

### 0.1 AWS 로그인

1. https://aws.amazon.com 접속 → **로그인** (또는 https://console.aws.amazon.com )
2. 상단 **리전**(예: 아시아 태평양(서울)) 선택

### 0.2 EC2 대시보드로 이동

1. 상단 검색창에 **EC2** 입력 → **EC2** 클릭
2. 왼쪽 메뉴 **인스턴스** → **인스턴스** 클릭
3. **인스턴스 시작** 버튼 클릭

### 0.3 인스턴스 설정

| 단계 | 선택/입력 |
|------|------------|
| **이름** | 예: `policy-server` (원하는 이름) |
| **OS(이미지)** | **Amazon Linux 2** 또는 **Ubuntu** |
| **인스턴스 유형** | **t2.micro** (무료 티어) 또는 t3.small |
| **키 페어** | **새 키 페어 생성** → 이름 입력(예: policy-key) → **.pem 파일 다운로드** 후 안전한 곳에 보관 (SSH 접속 시 필요) |
| **네트워크 설정** | **편집** 클릭 후 아래처럼 설정 |

**보안 그룹(방화벽) 규칙 추가:**

| 유형 | 포트 | 소스 | 설명 |
|------|------|------|------|
| SSH | 22 | 내 IP (또는 0.0.0.0/0) | 터미널 접속용 |
| 사용자 지정 TCP | 8000 | 0.0.0.0/0 | 웹 앱 접속용 (또는 HTTP 80 사용 시 80) |

- **스토리지**: 기본 8GB 그대로 두거나 필요 시 늘리기

### 0.4 인스턴스 시작

1. **인스턴스 시작** 버튼 클릭
2. 목록에 인스턴스가 보이면, **인스턴스 ID** 옆 **퍼블릭 IP**를 복사해 둡니다. (예: 13.209.xx.xx)

### 0.5 SSH로 접속하기

**Windows (PowerShell 또는 CMD):**

```cmd
cd 폴더경로
ssh -i "policy-key.pem" ec2-user@퍼블릭IP
```

- Amazon Linux: 사용자 이름 **ec2-user**
- Ubuntu: 사용자 이름 **ubuntu** → `ssh -i "policy-key.pem" ubuntu@퍼블릭IP`
- `.pem` 파일 경로는 본인이 저장한 위치로 바꾸세요.

처음 접속 시 "Are you sure you want to continue connecting?" 나오면 **yes** 입력.

---

## 1. 서버 준비 (EC2)

- **OS**: Amazon Linux 2 또는 Ubuntu 등
- **보안 그룹**: SSH(22), **HTTP(80)** 또는 **커스텀 TCP 8000** 인바운드 허용
- **탄력적 IP** (선택): 고정 IP 쓰려면 할당 후 인스턴스에 연결

---

## 2. 서버에 프로젝트 올리기

### 방법 A: Git 사용 (권장)

서버에서:

```bash
sudo yum install -y git   # Amazon Linux
# 또는  sudo apt install -y git   # Ubuntu

cd /home/ec2-user   # 또는 본인 홈
git clone <여기에_본인_저장소_URL> Policy
cd Policy
```

(저장소가 비공개면 SSH 키나 토큰 설정 필요.)

**중요: AWS에서 PDF가 반드시 있어야 합니다.** `data/pdf/공약/` 등에 PDF가 없으면 서버가 시작 시 `RuntimeError`로 중단됩니다. 아래 중 하나를 반드시 적용하세요.

| 옵션 | 설명 |
|------|------|
| **A. 이미지/배포에 포함** | Dockerfile에서 `COPY data/pdf /app/data/pdf` 또는 SCP/rsync로 `data/pdf` 전체를 서버에 올림. `.dockerignore`에 `data/pdf`가 있으면 제거. |
| **B. S3에서 내려받기** | `.env`에 `PDF_S3_URI=s3://버킷명/경로/` 설정. 시작 시 공약 폴더가 비어 있으면 `aws s3 sync`로 다운로드 후 진행. (AWS CLI 설치 및 권한 필요) |
| **C. EFS 마운트** | `data/pdf`를 EFS로 마운트. 시작 self-check에서 PDF 개수 검증. |

### 방법 B: 로컬에서 파일 복사 (SCP)

**Windows (PowerShell 또는 CMD)**에서 프로젝트 폴더로 간 뒤:

```bash
scp -i "키파일.pem" -r . ec2-user@<서버_공인IP>:/home/ec2-user/Policy/
```

`키파일.pem`은 EC2 인스턴스 생성 시 받은 키, `<서버_공인IP>`는 EC2 퍼블릭 IP로 바꾸세요.

---

## 3. 서버에서 Python 및 실행 환경 만들기

SSH로 접속한 뒤:

```bash
cd /home/ec2-user/Policy   # 프로젝트 경로에 맞게

# Python 3 설치 (없을 때)
# Amazon Linux 2:
sudo yum install -y python3.11 python3.11-pip
# Ubuntu:
# sudo apt update && sudo apt install -y python3.11 python3.11-venv

# 가상환경 생성 및 패키지 설치
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Ubuntu에서 한글 PDF가 잘 안 읽힐 때** (로컬과 다르게 추출되거나 빈 텍스트가 많을 때):

```bash
# 한글 폰트 설치 후 서버 재시작 (PDF 텍스트 추출 품질 개선)
sudo apt update
sudo apt install -y fonts-noto-cjk fonts-nanum
```

설치 후 앱을 한 번 재시작하면, 같은 PDF도 더 안정적으로 읽힐 수 있습니다.

### 로컬과 AWS 출력을 최대한 맞추는 방법

1. **같은 PDF 추출기 사용**  
   `.env`에 다음을 넣으면 로컬·AWS 모두 **pdfplumber**만 사용합니다. (기본값이 이미 pdfplumber입니다.)
   ```
   PDF_EXTRACTOR=pdfplumber
   ```

2. **수치로 비교**  
   로컬과 AWS에서 각각 다음 주소를 열어 보세요.  
   - `http://로컬주소:8000/api/debug/context-summary`  
   - `http://AWS주소:8000/api/debug/context-summary`  
   `platform`, `pledges`, `regional`의 `files_loaded`, `total_chars`가 비슷해야 합니다.  
   AWS에서 `total_chars`가 현저히 작으면 PDF 추출이 다르게 되고 있는 것이므로, 출력 차이의 원인일 수 있습니다.

3. **인덱스를 로컬에서 만들어서 AWS에 올리기 (선택)**  
   로컬에서 PDF가 잘 읽히므로, **로컬에서 서버를 한 번 실행**해 인덱스를 만든 뒤,  
   `data/index_cache/` 폴더 전체를 AWS로 복사합니다.  
   AWS에서는 같은 `data/pdf/`와 `data/index_cache/`를 쓰면, 검색(verify)에 쓰이는 청크·임베딩이 로컬과 동일해집니다.  
   (앱 시작 시 캐시가 있으면 재빌드하지 않습니다. PDF를 바꾸지 않았다면 캐시만 배포해도 됩니다.)

### .env 파일 만들기

서버에도 환경변수가 필요합니다. **로컬 .env를 그대로 복사**해도 되고, 아래처럼 예시 파일을 써도 됩니다.

```bash
# 예시 파일을 .env로 복사 (OPENAI_API_KEY만 수정하면 됨)
cp .env.aws.example .env
nano .env   # OPENAI_API_KEY=sk-proj-여기에본인키 부분만 본인 키로 수정
```

`.env.aws.example`에는 로컬과 동일한 설정(USE_OPENAI_VECTOR_STORE=1, CHAT_MODEL=gpt-5.2 등)이 이미 들어 있습니다. **수동으로 따로 바꿀 필요 없습니다.**

---

## 4. 서버에서 앱 실행

### 한 번만 실행 (테스트용)

```bash
cd /home/ec2-user/Policy
source .venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://<서버_공인IP>:8000` 으로 접속해 보세요.  
**보안 그룹에서 8000 포트 인바운드 허용**이 되어 있어야 합니다.

### 워커 1개로 실행 (레이스 확인용)

멀티워커에서 인덱스가 비는 현상이 있으면, 우선 **워커 1개**로 실행해 보세요.

```bash
# uvicorn 단일 프로세스 (기본)
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# gunicorn 사용 시 워커 1개
WEB_CONCURRENCY=1 gunicorn -w 1 -k uvicorn.workers.UvicornWorker backend.main:app --bind 0.0.0.0:8000
```

워커 1개로 정상화되면 멀티워커 레이스가 원인이므로, 앱은 `build.lock`으로 보정되어 있습니다. 필요 시 워커 수를 다시 늘려도 됩니다.

### 배포 성공 확인

다음 두 API가 **정상 값**을 보여야 배포 성공입니다.

- **GET /api/debug/fs** — `pdf_dir_exists: true`, `folders.공약.pdf_count > 0`, `sample_names`에 파일명 나옴
- **GET /api/debug/index** — `pledge_vectors > 0`, `platform_vectors`, `regional_vectors` 확인

둘 중 하나라도 0이거나 비정상이면 서버는 시작 단계에서 이미 `RuntimeError`로 떨어져 있거나, 다른 인스턴스/경로를 보고 있을 수 있습니다.

### 계속 켜 두기: systemd 서비스 (권장)

서버가 재부팅돼도 앱이 자동으로 떠 있게 하려면:

```bash
sudo nano /etc/systemd/system/policy-app.service
```

아래 내용 넣고, `User`, `WorkingDirectory`, `ExecStart` 경로를 본인 환경에 맞게 수정:

```ini
[Unit]
Description=AI 공약 멘토링 서버
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/Policy
Environment="PATH=/home/ec2-user/Policy/.venv/bin"
ExecStart=/home/ec2-user/Policy/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

저장 후:

```bash
sudo systemctl daemon-reload
sudo systemctl enable policy-app
sudo systemctl start policy-app
sudo systemctl status policy-app
```

이후:

- 중지: `sudo systemctl stop policy-app`
- 재시작: `sudo systemctl restart policy-app`
- 로그: `journalctl -u policy-app -f`

### systemd 없을 때: 한 번에 재시작 (sudo 불필요)

```bash
cd /home/ec2-user/Policy
./restart_server.sh
```

또는 **코드 업데이트 + 재시작** 한 번에:

```bash
./update.sh
```

(최초 1회: `chmod +x restart_server.sh update.sh`)

---

## 5. 80번 포트로 접속하려면 (Nginx, 선택)

80으로 접속하고 나중에 HTTPS를 붙이려면 Nginx를 앞단에 둡니다.

```bash
# Amazon Linux 2
sudo yum install -y nginx
# Ubuntu
# sudo apt install -y nginx

sudo nano /etc/nginx/conf.d/policy.conf
```

다음 내용 추가:

```nginx
server {
    listen 80;
    server_name _;   # 도메인 쓰면 여기 넣기 (예: pledge.example.com)
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx
```

보안 그룹에서 **80 인바운드 허용**하면 `http://<서버_공인IP>` 로 접속 가능합니다.

---

## 6. 체크리스트

| 항목 | 확인 |
|------|------|
| 보안 그룹에 22(SSH), 8000(또는 80) 인바운드 허용 | |
| 서버에 프로젝트 복사/클론 | |
| `.venv` 만들고 `pip install -r requirements.txt` | |
| `.env`에 `OPENAI_API_KEY` 설정 | |
| `data/pdf/` 에 필요한 PDF 있음 | |
| uvicorn 또는 systemd로 앱 실행 | |
| 브라우저에서 `http://서버IP:8000` (또는 `:80`) 접속 | |

---

## 6-1. AWS 실행환경별 RAG 체크리스트 (검색 결과 0 방지)

### EB/EC2 (systemd, pm2, supervisor)

| 항목 | 확인 |
|------|------|
| `INDEX_CACHE_DIR`를 **영구 경로**로 설정 (예: `/app/data/index_cache`, EBS 마운트) | |
| Linux 기본값 `/tmp/index_cache`는 **재시작 시 삭제됨** — 반드시 ENV 또는 볼륨 지정 | |
| `WEB_CONCURRENCY=1` 또는 `uvicorn --workers 1` (멀티워커 시 인덱스 미공유 이슈) | |
| 작업 디렉터리(WorkingDirectory)가 프로젝트 루트 | |
| `data/pdf/`, `data/index_cache/` 쓰기 권한 | |
| 로그: `journalctl -u policy-app -f` 또는 pm2 logs | |

### ECS (Docker)

| 항목 | 확인 |
|------|------|
| task definition에 `INDEX_CACHE_DIR=/app/data/index_cache` 환경변수 | |
| **volume mount**: `index-cache` → `/app/data/index_cache` (EFS 또는 호스트 볼륨) | |
| `desiredCount > 1` 시: **모든 task가 EFS 등 공유 스토리지**에 인덱스 저장/로드 | |
| healthcheck: `GET /api/debug/index` 응답 `pledge_vectors > 0` | |
| CMD에 `--workers 1` 포함 | |

### ALB 뒤 multiple instances

| 항목 | 확인 |
|------|------|
| 인스턴스별 로컬 디스크(`/tmp`)는 **공유되지 않음** | |
| **공유 전략**: EFS 마운트로 `INDEX_CACHE_DIR`를 모든 인스턴스에서 동일 경로로 사용 | |
| 또는 인스턴스 1개만 RAG 검색 담당, 나머지는 리다이렉트 | |

### 디버그 API 확인

- `GET /api/debug/fs` — `pdf_dir_exists: true`, `folders.공약.pdf_count > 0`
- `GET /api/debug/index` — `pledge_vectors > 0`, `platform_vectors`, `regional_vectors`
- `GET /api/debug/vectorstore` — `persist_path`, `total_count`, `embedding_model_name`, `sample_doc`

`DEBUG_ENDPOINTS_ENABLED=0`이면 프로덕션에서 비활성화됨.

---

## 7. 문제 해결

- **접속 안 됨**: 보안 그룹에서 해당 포트(8000 또는 80) 인바운드 허용 여부 확인.
- **502 Bad Gateway**: Nginx 쓰는 경우 `sudo systemctl status policy-app` 로 앱이 8000에서 떠 있는지 확인.
- **504 Gateway Time-out** (분석 중): 당 부합 점검(/check, /api/pledge/verify)은 GPT·벡터 검색으로 1~2분 걸릴 수 있음. **프록시 타임아웃을 반드시 180초 이상(권장 300초)**으로 설정.
  - **Nginx**: `proxy_read_timeout 300s;` `proxy_connect_timeout 300s;` `proxy_send_timeout 300s;` (location / 또는 /check, /api 포함 블록에).
  - **AWS ALB**: 로드 밸런서 → 해당 타깃 그룹 → 속성 → 유휴 제한 시간(Idle timeout)을 **300초**로 변경.
  - 서버 측에서는 검색량·컨텍스트 길이를 줄여 응답 시간을 단축해 두었음. 그래도 60초 미만 프록시에서는 타임아웃이 날 수 있으므로 위 설정 필수.
- **타임아웃 원인 파악**: 서버 로그에서 `[check] started` / `[verify] started` 와 `completed in Xs` 를 확인.
  - `started` 만 있고 `completed` 가 없으면 → **프록시가 먼저 연결을 끊은 것**(Nginx/ALB 타임아웃). 위처럼 300초로 올리면 해결.
  - `completed in 120.5s` 처럼 60초 넘게 걸리면 → 백엔드는 정상 완료인데, 그 전에 프록시가 60초에 끊어서 504가 난 것. 역시 프록시 타임아웃 상향 필요.
  - 로그 보는 법: `journalctl -u policy-app -f` (systemd) 또는 앱이 출력하는 로그 파일.
- **API 키 오류**: 서버의 `Policy/.env` 에 `OPENAI_API_KEY`가 올바르게 들어 있는지 확인.
