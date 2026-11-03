# Contributing to watchmecode.live

Thanks for helping. Every change goes through a feature branch and a pull request.

## 1. Create a feature branch

```bash
git checkout main
git pull origin main
git checkout -b feature/short-description
```

Use `fix/` for bug fixes and `docs/` for documentation-only changes.

## 2. Make and check your change

```bash
uv sync
uv run pytest
uv run uvicorn api.index:app --reload
```

Exercise the change in the browser at http://127.0.0.1:8000. If you touch the editor bundle, rebuild it with `cd frontend && npm install && npm run build` and commit `static/vendor/codemirror.js`.

Keep secrets in `.env`. Never commit `.env` or credentials.

## 3. Open a pull request

```bash
git push -u origin HEAD
gh pr create --title "Short summary" --body "Why this change belongs in watchmecode.live"
```

The pull request description should explain:

- **Why** the change belongs in the repository: the interview problem it solves or the risk it removes.
- **What** changed, in a few bullets.
- **How you tested it**: pytest results and the browser flows you clicked through.

CI runs `uv run pytest` on every pull request, and Vercel posts a preview URL for the branch. Merge after CI passes and the preview looks right. Feature changes also get a line in [CHANGELOG.md](CHANGELOG.md).
