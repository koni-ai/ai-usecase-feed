# AI 활용사례 피드

공개된 AI 실제 활용사례를 매일 수집하고 한국어 카드로 가공해 보여주는
개인용 모바일 피드입니다.

## 바로 보기

- 모바일·PC 공용: https://koni-ai.github.io/ai-usecase-feed/
- GitHub 저장소: https://github.com/koni-ai/ai-usecase-feed

## 운영 구조

- 매일 오전 7시(Asia/Seoul) GitHub Actions 실행
- 기준 공개 소스 5개와 검증된 동적 RSS 소스 최대 3개, 하루 최대 30건
- 매주 HN Algolia·DEV 공개 API에서 신규 사이트를 찾고 선언된 RSS/Atom만 시험
- 신규 후보는 최소 7일 probation 후 품질 기준 통과 시에만 자동 활성화
- 최신순 라운드로빈으로 특정 채널의 일일 후보 독점을 방지
- 기존 Claude Max OAuth만 사용
- `ANTHROPIC_API_KEY` 및 종량제 API 사용 금지
- 성공한 실행만 데이터와 정적 HTML을 저장하고 GitHub Pages에 배포
- 실패 시 기존 Pages 버전을 유지

## 소스 순환 백엔드

- `discovery.yaml` — 무료·공개·읽기 전용 발견 인덱스
- `source_manager.py` — 후보 발굴, RSS 검증, 격리 시험, 승격, 일시정지, 복구
- `data/source_registry.json` — `probation / active / paused / retired` 이력
- `data/source_health.json` — 최근 30회 성공·실패·수집·선택 통계
- 1~2회 연속 실패는 `failing`, 3회는 `warning`, 5회는 `paused`
- paused 소스는 주간 저빈도 probe 성공 후에만 복구
- 레지스트리는 최대 100개, 퇴출 도메인 차단 이력은 최대 500개로 제한
- probation 소스는 일일 Claude 가공과 모바일 피드에 들어가지 않음
- DNS와 redirect 목적지는 매 요청 전에 공개 IP인지 검사한다. Python URL
  연결 시점의 DNS 재해석까지 고정하지는 못하므로 DNS rebinding 잔여 위험은
  공개 GitHub hosted runner의 격리 환경과 응답 크기·프로토콜 제한으로 완화한다.
- 프런트엔드, 카드 스키마, 북마크·읽음 저장 방식은 기존과 동일

주간 발견을 저장하지 않고 점검:

```powershell
python -B source_manager.py discover --dry-run
```

## 로컬 실행

```powershell
python -m pip install -r requirements.txt
python -B run.py
```

결과는 `site/index.html`에서 확인할 수 있습니다.

## GitHub Secret

Actions에는 `CLAUDE_CODE_OAUTH_TOKEN` 하나만 필요합니다.
토큰은 `claude setup-token`으로 생성하고 GitHub Secret에만 저장합니다.
파일, 커밋, 로그에는 토큰을 기록하지 않습니다.
