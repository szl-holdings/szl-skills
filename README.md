# SZL Skills

Fail-closed evidence and decision skills for Claude (Claude Science, Claude Code, claude.ai).

## Import

Claude Science: Skills > Import from GitHub, paste:

    szl-holdings/szl-skills@v0.1.0

Claude Code: copy a folder from skills/ into .claude/skills/ or ~/.claude/skills/.

## Skills

| Skill | What it does | What it does not do |
|---|---|---|
| typesafe-ai | Uses TypeSafe Jev (Choice / Noul / Score) as an optional second reader for evidence-class triage. Fail-closed: any error gives UNAVAILABLE, never PASS. | Not a gate. Not TypeScript, Zod, Pydantic, mypy, or JSON Schema. Never marks anything LIVE. |
| szl-governed-decision | Wraps a classifier, policy engine, or System One model so each decision carries its own evidence. | Does not prove a model output is true. Receipts cover integrity and origin only. |

## Setup for typesafe-ai

Use your own TypeSafe key. Save it as a credential (Customize > Credentials > Add Credential, generic token, name typesafe) or as the environment variable TYPESAFE_API_KEY. Never paste it into chat.

## Provenance

Built from szl-holdings/szl-typesafe-triage at commit 8d73dd23f640aace362172fff55d82ef7de63f98 (typesafe-ai source: GITHUB). License: Apache-2.0 (see LICENSE and NOTICE).