"""Every language has every text, with the same {placeholders}; every text the page or the
server asks for exists. Needs Node.js to read the translations file (skipped without it)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PKG = ROOT / "swimtunes"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node.js is needed to read static/i18n.js")


@pytest.fixture(scope="module")
def i18n():
    script = ("const fs = require('fs'); const code = fs.readFileSync(process.argv[1], 'utf8');"
              "process.stdout.write(JSON.stringify(new Function(code + '; return {LANG_NAMES, I18N};')()));")
    out = subprocess.run([NODE, "-e", script, str(PKG / "static" / "i18n.js")],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def unknown(keys, en):
    """Keys not in the English texts. A key ending in "_" is a prefix completed by the code."""
    return sorted(k for k in keys if (not any(e.startswith(k) for e in en) if k.endswith("_") else k not in en))


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


def test_every_language_is_complete(i18n):
    en = i18n["I18N"]["en"]
    assert set(i18n["LANG_NAMES"]) == set(i18n["I18N"])
    for lang, texts in i18n["I18N"].items():
        assert set(texts) == set(en), f"{lang}: missing {sorted(set(en) - set(texts))[:10]}, extra {sorted(set(texts) - set(en))[:10]}"
        for key, text in texts.items():
            assert placeholders(text) == placeholders(en[key]), f"{lang} {key}: {text!r}"
            assert text.count("|") == en[key].count("|"), f"{lang} {key}: singular|plural forms differ"
            assert text.strip(), f"{lang} {key} is empty"


def test_every_text_the_page_uses_exists(i18n):
    en = i18n["I18N"]["en"]
    page = (PKG / "static" / "app.js").read_text(encoding="utf-8") + (PKG / "templates" / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r"""\bt\(\s*'([a-z_]+\.[a-z0-9_]+)'""", page))
    used |= set(re.findall(r"""data-(?:i18n(?:-html|-ph)?|tip)="([a-z_]+\.[a-z0-9_]+)\"""", page))
    missing = unknown(used, en)
    assert not missing, missing


def test_every_server_message_has_a_translation(i18n):
    en = i18n["I18N"]["en"]
    used = set()
    for f in PKG.glob("*.py"):
        used |= set(re.findall(r"""\bmsg\(\s*["']([a-z_]+\.[a-z0-9_]+)["']""", f.read_text(encoding="utf-8")))
    missing = unknown(used, en)
    assert not missing, missing


def test_page_scripts_are_valid_javascript():
    for f in ("app.js", "i18n.js"):
        r = subprocess.run([NODE, "--check", str(PKG / "static" / f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
