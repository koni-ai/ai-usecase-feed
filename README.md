# AI 활용사례 피드

공개된 AI 실제 활용사례를 매일 수집하고 한국어 카드로 가공해 보여주는
개인용 모바일 피드입니다.

## 바로 보기

- 모바일·PC 공용: https://koni-ai.github.io/ai-usecase-feed/
- GitHub 저장소: https://github.com/koni-ai/ai-usecase-feed

## 운영 구조

- 매일 오전 7시(Asia/Seoul) GitHub Actions 실행
- 공개 소스 5개 수집, 하루 최대 30건
- 기존 Claude Max OAuth만 사용
- `ANTHROPIC_API_KEY` 및 종량제 API 사용 금지
- 성공한 실행만 데이터와 정적 HTML을 저장하고 GitHub Pages에 배포
- 실패 시 기존 Pages 버전을 유지

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
