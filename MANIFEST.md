# Release Manifest

This package is a sanitized source snapshot generated from the working tree. It does not include Git history.

## Included top-level components

- README.md
- MANIFEST.md
- RELEASE_AUDIT.md
- LICENSE
- pyproject.toml / uv.lock / .python-version
- retrieval.py
- experiments/retrieval_tasks_backend/ selected main-method source files
- skillsbench_private/ runtime support modules
- scripts/ selected environment helper scripts
- deployment/runner/ selected sanitized runner files
- global_skill_pool/
- tasks/
- build_pipeline/
- retrieval graph and embedding artifacts
- examples/api.env.example

## Explicitly excluded

- .git/
- .codex/
- .claude/
- baselines/
- runs/, jobs/, artifacts/, and outputs/
- phase1_claude_runs/
- caches, bytecode, logs, temporary files
- api.env, runner.env, .env, private keys, and credential files
- local Codex runtime bundles and local test fixtures containing machine paths
