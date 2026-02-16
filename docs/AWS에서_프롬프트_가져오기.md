# AWS(EC2)에서 당 부합 점검 프롬프트 파일 가져오기

EC2 서버에 있는 `당_부합_점검_시스템.txt`, `당_부합_점검_유저.txt`를 로컬 `prompts/` 폴더로 복사하는 방법입니다.

**중요: 파일을 가져올 때는 `ssh`가 아니라 `scp` 명령을 사용합니다.** (`ssh`는 원격 로그인용, `scp`는 파일 복사용)

## 준비물

- EC2 **퍼블릭 IP** (또는 호스트명)
- EC2 접속용 **.pem 키 파일** 경로
- EC2 프로젝트 경로 (예: `/home/ec2-user/Policy`)

---

## 방법 1: 두 파일만 가져오기 (권장)

**PowerShell 또는 CMD**에서 **로컬 프로젝트 폴더(`c:\policy`)**로 이동한 뒤 실행:

```powershell
# 키 파일과 EC2 IP를 본인 환경에 맞게 바꾸세요
$KEY = "C:\경로\policy-key.pem"
$EC2 = "ec2-user@13.209.xx.xx"   # 실제 퍼블릭 IP로 변경

# 시스템 프롬프트
scp -i $KEY "${EC2}:/home/ec2-user/Policy/prompts/당_부합_점검_시스템.txt" prompts/

# 유저 프롬프트
scp -i $KEY "${EC2}:/home/ec2-user/Policy/prompts/당_부합_점검_유저.txt" prompts/
```

한 줄씩 실행하려면:

```powershell
cd c:\policy

scp -i "C:\경로\policy-key.pem" ec2-user@13.209.xx.xx:/home/ec2-user/Policy/prompts/당_부합_점검_시스템.txt prompts/
scp -i "C:\경로\policy-key.pem" ec2-user@13.209.xx.xx:/home/ec2-user/Policy/prompts/당_부합_점검_유저.txt prompts/
```

- `C:\경로\policy-key.pem` → 실제 .pem 키 파일 경로
- `13.209.xx.xx` → EC2 퍼블릭 IP
- EC2에서 프로젝트 경로가 다르면 `/home/ec2-user/Policy` 부분을 해당 경로로 바꾸세요 (예: Ubuntu면 `ubuntu@IP` 사용).

---

## 방법 2: prompts 폴더 통째로 가져오기

```powershell
cd c:\policy
scp -i "C:\경로\policy-key.pem" -r ec2-user@13.209.xx.xx:/home/ec2-user/Policy/prompts prompts_from_aws
```

실행 후 `prompts_from_aws` 폴더가 생깁니다. 필요한 파일만 `prompts/`로 복사한 뒤 `prompts_from_aws`는 삭제하면 됩니다.

---

## EC2 프로젝트 경로가 다른 경우

서버에 SSH 접속해서 실제 경로 확인:

```bash
ssh -i "policy-key.pem" ec2-user@13.209.xx.xx
pwd
ls -la Policy/prompts/
# 또는
find /home -name "당_부합_점검_유저.txt" 2>/dev/null
```

찾은 경로를 위 `scp` 명령의 경로에 넣으면 됩니다.

---

## 요약

| 목적 | 명령 |
|------|------|
| 시스템 프롬프트만 | `scp -i "키.pem" ec2-user@IP:/home/ec2-user/Policy/prompts/당_부합_점검_시스템.txt prompts/` |
| 유저 프롬프트만 | `scp -i "키.pem" ec2-user@IP:/home/ec2-user/Policy/prompts/당_부합_점검_유저.txt prompts/` |
| prompts 폴더 전체 | `scp -i "키.pem" -r ec2-user@IP:/home/ec2-user/Policy/prompts prompts_from_aws` |

실행 전 반드시 `cd c:\policy`로 프로젝트 루트에 있는지 확인하세요.
