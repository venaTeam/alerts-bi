from pathlib import Path

from src.hashing import sha256_text
from src.llm.prompt import GUIDE_FILES, build_prompt

ROOT = Path(__file__).resolve().parents[2]


def test_prompt_version_pins_all_instructions_and_complete_guides() -> None:
    prompt = build_prompt(ROOT)
    for filename in GUIDE_FILES:
        assert (ROOT / "docs" / filename).read_bytes().decode("utf-8") in prompt.system_prompt
    # .gitattributes pins guide bytes to LF on every platform, matching SQL provenance.
    fingerprints = {"1.3.0": "f9754a11f4e9e0ba438c1f2d9062fa9511d57dc67789346ff01fc4e18489b794"}
    assert sha256_text(prompt.system_prompt) == fingerprints[prompt.prompt_version]
    assert prompt.system_prompt_hash == sha256_text(prompt.system_prompt)
