"""Version identifiers frozen onto every run (design section 6, "Storage and output shape").

A movement in a team's numbers must always be attributable, so each of these is bumped
deliberately and never edited in place:

- ``RULESET_VERSION`` covers the R1-R10 deterministic rules AND the P1-P11 LLM principle
  catalogue. Adding a phrase to an R1/R2/R3/R10 catalogue is a ruleset change.
- ``PROMPT_VERSION`` covers the classification instructions, the two guides carried in the
  cached prefix, and the R/P catalogue as presented to the model.
- ``PARSER_VERSION`` covers the panel-SQL interpretation (suppression and identity leaves); cached panel parses are keyed by
  ``(sql_text_hash, parser_version)`` so a parser change re-derives rather than reusing.

``model_version`` is not here: it is supplied by configuration (the exact on-prem
deployment identifier) and recorded per run.

The language port preserved these versions. Later prompt improvements receive their own
version; the rule and principle catalogues remain unchanged.
"""

RULESET_VERSION = "1.0.0"
# 1.2.0 (2026-09-24): evidence guidance, explicit applicability and untrusted alert text.
PROMPT_VERSION = "1.2.0"
# 1.1.0 (2026-10-01): positive identity leaves are interpreted for `unseen`.
PARSER_VERSION = "1.1.0"

APP_VERSION = "0.1.0"
