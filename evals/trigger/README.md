# Trigger evaluation sets

One file per skill: 20 user queries, 10 that should load the skill and 10 near-misses that should not
(same keywords, different need). The format follows the Anthropic skill-creator guidance for description
optimization: run each query against the model that powers the session, record the trigger rate, and
revise the description for the failures.

These sets have not been executed yet. Trigger rates for every skill are NOT_MEASURED until someone runs
them in Claude Code (`claude -p`) or Claude Science and records the results here with the model id and date.
The sets are a test fixture for the descriptions, not evidence that the descriptions work.
