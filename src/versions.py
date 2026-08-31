"""Version identifiers frozen onto every run (design section 6, "Storage and output shape").

A movement in a team's numbers must always be attributable, so each of these is bumped
deliberately and never edited in place:

- ``RULESET_VERSION`` covers the R1-R10 deterministic rules AND the P1-P11 LLM principle
  catalogue. Adding a phrase to an R1/R2/R3/R10 catalogue is a ruleset change.
- ``PROMPT_VERSION`` covers the classification instructions, the two guides carried in the
  cached prefix, and the R/P catalogue as presented to the model.
- ``PARSER_VERSION`` covers the panel-SQL parser; cached panel parses are keyed by
  ``(sql_text_hash, parser_version)`` so a parser change re-derives rather than reusing.

``model_version`` is not here: it is supplied by configuration (the exact on-prem
deployment identifier) and recorded per run.

These values are carried over unchanged from the superseded JavaScript implementation. The
port changed the language, not the ruleset, so bumping them would falsely signal that what
counts as a bad alert had changed.
"""

RULESET_VERSION = "1.0.0"
# 1.1.0 (2026-08-31): the prompt states the numeric severity scale, because alerts carry
# severity as a number and the guides reason about it by name.
PROMPT_VERSION = "1.1.0"
PARSER_VERSION = "1.0.0"

APP_VERSION = "0.1.0"
