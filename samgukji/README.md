# 삼국지 365 일일 피드

비공개 Sites 앱이 읽는 공개 콘텐츠 엔드포인트다. 기존 아비투스 자동화의 오전
8시 KST GitHub Actions 구조를 삼국지 프로젝트가 승계한다.

- 시작일: 2026-09-01 (Day 1)
- 실행: 매일 08:00 KST, 주말 포함
- 한 번에 최대 한 편만 추가
- Day 1~14: 승인 과정에서 완성된 대기열을 날짜에 맞춰 순차 발행
- Day 15~365: 커리큘럼의 다음 날만 조사·작성
- 이미지: 공개 라이선스 원본을 검증하고 3:2 대표 이미지로 저장
- 비용 경계: `CLAUDE_CODE_OAUTH_TOKEN` 구독 경로만 허용, 종량제 API 환경변수는 즉시 실패
- 실패 경계: 본문·출처·이미지·JSON 검증을 모두 통과한 경우에만 커밋과 Pages 배포

로컬 검증:

```powershell
python -B samgukji/generate_daily.py --validate-only
python -B samgukji/generate_daily.py --date 2026-09-02 --dry-run
```

공개 데이터 주소:

`https://koni-ai.github.io/ai-usecase-feed/samgukji/feed.json`
