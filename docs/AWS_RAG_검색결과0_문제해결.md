# AWS RAG/PDF 검색 결과 0 문제 해결 가이드

## 1) Vector DB/Index Persist 경로 추적

이 프로젝트는 **FAISS** 사용 (Chroma/Pinecone 미사용). 단일 경로 `INDEX_CACHE_DIR`로 인덱스 3개 저장.

| 구분 | 코드 위치 | 경로/컬렉션 | 값 |
|------|-----------|-------------|-----|
| **인덱싱** | `index_builder.py` | cache_dir | `Path(INDEX_CACHE_DIR)` |
| **인덱싱** | `index_builder.py` | 저장 파일 | `{cache_dir}/platform.faiss`, `pledge.faiss`, `regional.faiss`, `*_meta.pkl`, `*_hashes.pkl` |
| **검색** | `main.py` startup | load | `build_all_indexes()` → 동일 `INDEX_CACHE_DIR`에서 로드 |
| **설정** | `config.py` | INDEX_CACHE_DIR | Windows: `data/index_cache`, Linux: `/tmp/index_cache` (휘발) |

**결론:** 인덱싱·검색 모두 `INDEX_CACHE_DIR` 사용. 단, Linux 기본값 `/tmp`는 휘발성.

---

## (A) 원인 후보 3개 (우선순위 순)

### 1순위: INDEX_CACHE_DIR이 휘발성 경로(/tmp) 사용

**코드 근거:**
```python
# backend/config.py:44-52
_def_cache_env = os.getenv("INDEX_CACHE_DIR", "").strip()
if _def_cache_env:
    INDEX_CACHE_DIR = Path(_def_cache_env).resolve()
else:
    if sys.platform == "win32":
        INDEX_CACHE_DIR = (ROOT_DIR / "data" / "index_cache").resolve()
    else:
        INDEX_CACHE_DIR = Path("/tmp/index_cache").resolve()  # ← Container/EC2 재시작 시 삭제됨
```

**문제:** Linux 기본값 `/tmp/index_cache`는 컨테이너/인스턴스 재시작 시 사라짐. 매번 인덱스 재빌드 필요.  
재빌드 실패(API 타임아웃, PDF 없음, locale 등) 시 빈 인덱스로 시작 → 검색 결과 0.

**우선순위:** 가장 높음. AWS 배포 시 반드시 영구 스토리지 경로 지정 필요.

---

### 2순위: 멀티워커/멀티인스턴스 시 인덱스 미공유

**코드 근거:**
```python
# backend/main.py:47, 164-165
_indexes = None  # 전역 인메모리, 프로세스별 독립

# startup_event에서 build_all_indexes() 호출
_indexes = build_all_indexes(force_rebuild=False)
```

- Dockerfile CMD: `uvicorn ...` (workers 미지정 → 기본 1)
- EB/ECS/EC2에서 `gunicorn -w 2` 또는 `WEB_CONCURRENCY=2` 사용 시 **워커별로 별도 프로세스**
- 각 워커는 `startup_event`를 독립 실행 → FileLock으로 빌드 동시화는 되지만, **인덱스는 각 프로세스 메모리에 복사**
- **ALB 뒤 multiple EC2/ECS tasks** 시: 인스턴스마다 `/tmp` 또는 로컬 디스크가 다름 → **인덱스 파일 공유 불가**

**문제:** desiredCount>1 인 ECS에서 task A가 인덱스 빌드, task B는 별도 볼륨 → B가 빈 인덱스 또는 재빌드 실패 가능.

**우선순위:** 2순위. ECS desiredCount>1 또는 gunicorn multi-worker 사용 시 발현.

---

### 3순위: 임베딩 차원 하드코딩 불일치

**코드 근거:**
```python
# backend/config.py:39
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")

# backend/embeddings.py:58 — 실패 시 3072로 폴백
embeddings.extend([[0.0] * 3072 for _ in batch])  # text-embedding-3-large 가정

# backend/vector_index.py:39, 154 — VectorIndex.load(dimension=3072)
# backend/main.py:167 — VectorIndex(dimension=3072, ...)
```

- `EMBEDDING_MODEL`을 `text-embedding-3-small`(1536차원)로 바꾸면:
  - 인덱싱: 1536차원
  - 로드: `VectorIndex.load(..., dimension=3072)` → FAISS 인덱스와 불일치

**문제:** 모델 변경 시 차원 불일치로 검색 실패 또는 빈 결과.

**우선순위:** 3순위. 모델 변경하지 않으면 현재는 3072로 일관.

---

## (B) 통일된 ENV 설계

| ENV | 용도 | 기본값 | AWS 권장 |
|-----|------|--------|----------|
| `INDEX_CACHE_DIR` | FAISS 인덱스 저장 경로 | Windows: `data/index_cache`, Linux: `/tmp/index_cache` | **EBS/EFS 마운트 경로** (예: `/app/data/index_cache`) |
| `EMBEDDING_MODEL` | OpenAI 임베딩 모델 | `text-embedding-3-large` | 동일 |
| `EMBEDDING_DIMENSION` | 임베딩 차원 (선택) | 모델별 자동(3072/1536) | 설정 시 `EMBEDDING_MODEL`과 일치 필수 |
| `WEB_CONCURRENCY` | uvicorn/gunicorn 워커 수 | 1 | **1** (RAG 검색 시) |
| `DEBUG_ENDPOINTS_ENABLED` | /api/debug/* 노출 | `1` | 프로덕션: `0` |

**참고:** Chroma/Pinecone 미사용. FAISS 단일 경로로 인덱스 3개(platform, pledge, regional) 저장.

---

## (C) 패치 적용 및 검증

### 패치 1: config.py — EMBEDDING_DIMENSION, INDEX_CACHE_DIR 문서화

```diff
# backend/config.py
+ # 임베딩 차원 수 (text-embedding-3-large=3072, text-embedding-3-small=1536)
+ _embed_dim = os.getenv("EMBEDDING_DIMENSION", "").strip()
+ if _embed_dim:
+     EMBEDDING_DIMENSION = int(_embed_dim)
+ else:
+     EMBEDDING_DIMENSION = 3072 if "large" in EMBEDDING_MODEL.lower() else 1536
```

### 패치 2: Dockerfile — INDEX_CACHE_DIR, workers=1

```diff
# Dockerfile
+ ENV INDEX_CACHE_DIR=/app/data/index_cache
+ ENV PYTHONPATH=/app
- CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
+ CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

### 패치 3: /api/debug/vectorstore 엔드포인트

```python
@app.get("/api/debug/vectorstore")
def debug_vectorstore():
    """persist_path, total_count, embedding_model, embedding_dim, sample_doc 반환."""
    # DEBUG_ENDPOINTS_ENABLED=0 시 404
```

### 패치 4: /api/debug/search에 filter 없이 top_k만 조회 옵션

- 현재 `VectorIndex.search`에 filter 없음 — 이미 top_k만 사용. 추가 필터 없음.

---

## 로컬 검증

```bash
# 1. 인덱스가 data/index_cache에 생성되는지 확인
python -c "from backend.config import INDEX_CACHE_DIR; print(INDEX_CACHE_DIR)"
# Windows: ...\Policy\data\index_cache
# Linux: /tmp/index_cache (또는 INDEX_CACHE_DIR 설정 시)

# 2. 서버 실행 후
curl http://localhost:8000/api/debug/index
# pledge_vectors > 0 확인

curl "http://localhost:8000/api/debug/search?source=pledge&q=신구연금&top_k=5"
# 결과가 비어있지 않은지 확인
```

---

## AWS Docker 검증

```bash
# 1. INDEX_CACHE_DIR를 영구 볼륨으로 지정
docker run -e INDEX_CACHE_DIR=/app/data/index_cache \
  -v /path/on/host/index_cache:/app/data/index_cache \
  policy-app

# 2. 재시작 후에도 인덱스 유지 확인
curl http://<ec2-ip>:8000/api/debug/vectorstore
curl http://<ec2-ip>:8000/api/debug/index
```

---

## ECS Task Definition 예시

```json
{
  "volumes": [
    { "name": "index-cache", "host": {} }
  ],
  "containerDefinitions": [{
    "environment": [
      { "name": "INDEX_CACHE_DIR", "value": "/app/data/index_cache" },
      { "name": "WEB_CONCURRENCY", "value": "1" }
    ],
    "mountPoints": [
      { "sourceVolume": "index-cache", "containerPath": "/app/data/index_cache" }
    ]
  }]
}
```

---

## EBS/EFS 마운트 (EC2)

```bash
# /app/data 디렉터리를 EBS 볼륨에 마운트
sudo mkdir -p /app/data
sudo mount /dev/xvdf /app/data  # 또는 /etc/fstab에 영구 등록

# .env에
INDEX_CACHE_DIR=/app/data/index_cache
```
