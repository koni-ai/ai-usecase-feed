# Habitus daily content endpoint

비공개 Sites 모바일 앱이 읽는 공개 라이선스 예술 콘텐츠 JSON을 매일 08:00
KST에 생성합니다. GitHub Actions cron은 한국 표준시가 UTC+9이고 서머타임이
없으므로 전날 23:00 UTC로 고정했습니다.

- 기존 `CLAUDE_CODE_OAUTH_TOKEN` 구독 Secret만 사용
- 유료 Anthropic API 환경변수가 있으면 즉시 실패
- Met Open Access의 public-domain 이미지 후보만 사용
- 생성·검증 성공 후에만 feed와 Pages artifact 갱신
- 실패하면 마지막 정상 JSON과 비공개 앱의 번들 피드 유지

