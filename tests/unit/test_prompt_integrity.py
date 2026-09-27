from pathlib import Path

from src.hashing import sha256_text
from src.llm.prompt import GUIDE_FILES, build_prompt

ROOT = Path(__file__).resolve().parents[2]


def test_prompt_version_pins_all_instructions_and_complete_guides() -> None:
    prompt = build_prompt(ROOT)
    for filename in GUIDE_FILES:
        assert (ROOT / "docs" / filename).read_bytes().decode("utf-8") in prompt.system_prompt
    # .gitattributes pins guide bytes to LF on every platform, matching SQL provenance.
    fingerprints = {"1.2.0": "29dd2d43ddcfb82160b4f9071d1dcf1716220869f48230a3da10be932f628db1"}
    assert sha256_text(prompt.system_prompt) == fingerprints[prompt.prompt_version]
    assert prompt.system_prompt_hash == sha256_text(prompt.system_prompt)
