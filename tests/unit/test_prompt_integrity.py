from pathlib import Path

from src.hashing import sha256_text
from src.llm.prompt import GUIDE_FILES, build_prompt

ROOT = Path(__file__).resolve().parents[2]


def test_prompt_version_pins_all_instructions_and_complete_guides() -> None:
    prompt = build_prompt(ROOT)
    for filename in GUIDE_FILES:
        assert (ROOT / "docs" / filename).read_bytes().decode("utf-8") in prompt.system_prompt
    # .gitattributes pins guide bytes to LF on every platform, matching SQL provenance.
    fingerprints = {"1.3.0": "cee6f5390bcc292321df8bc64f47a6c9436ac581a6bc34fe56b174e13f89c205"}
    assert sha256_text(prompt.system_prompt) == fingerprints[prompt.prompt_version]
    assert prompt.system_prompt_hash == sha256_text(prompt.system_prompt)
