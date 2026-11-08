# Working rules for this project (read at the start of every session)

This project is built from `BUILD_SPEC.md` (the full specification). Progress lives in `docs/PROGRESS.md`.

## At the start of every session
1. Read `BUILD_SPEC.md` fully, then `docs/PROGRESS.md` (if it exists).
2. Tell me in 3–5 lines: which milestones are done, which milestone is next, and anything unfinished.
3. Wait for me to say "go" before writing code.

## One milestone at a time
- Build exactly ONE milestone (Section 14 of BUILD_SPEC.md), then STOP.
- Before stopping: run all tests, linters and type checks (all must pass) and update `docs/PROGRESS.md`.
- Then give me:
  1. What was built (short, plain language).
  2. How to test it myself, step by step: exact commands to run and what I should see (URLs, screens, outputs).
  3. The list of changed/added files.
  4. A suggested commit message: `M<n>: <milestone name>`.
- Do not start the next milestone until I say "continue".

## Git
- Never run `git commit`, `git push`, `git reset`, `git rebase` or anything that changes git history.
  I verify and commit myself. Read-only commands (`git status`, `git diff`) are fine.
- Keep `.gitignore` correct (must ignore `.env`, virtual environments, `node_modules`, build output).

## Secrets and `.env`
- A `.env` file already exists at the project root with my local values and API key.
  **Never overwrite, delete or print its secret values.**
- Use the variable names and local ports from `.env` in `docker-compose.yml` and config. Containers that need
  other hostnames (e.g. `postgres` instead of `localhost`) get them via `environment:` overrides in compose.
- If a new variable is needed: add it to `.env.example` with a comment, and tell me what to add to `.env`.

## Other rules
- This is a new project built from scratch. Do not look for or reuse code from any other folder.
- No real LLM calls in tests (use the fake LLM client).
- If something needs my action (a credential, an account, approving a design), stop and ask clearly.
- If you are unsure about a requirement, ask instead of guessing.
