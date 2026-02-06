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

### .env 파일 만들기

서버에도 API 키가 있어야 합니다.

```bash
nano .env
```

아래 한 줄 입력 후 저장 (Ctrl+O, Enter, Ctrl+X):

```
OPENAI_API_KEY=sk-proj-여기에본인키
```

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

## 7. 문제 해결

- **접속 안 됨**: 보안 그룹에서 해당 포트(8000 또는 80) 인바운드 허용 여부 확인.
- **502 Bad Gateway**: Nginx 쓰는 경우 `sudo systemctl status policy-app` 로 앱이 8000에서 떠 있는지 확인.
- **API 키 오류**: 서버의 `Policy/.env` 에 `OPENAI_API_KEY`가 올바르게 들어 있는지 확인.
