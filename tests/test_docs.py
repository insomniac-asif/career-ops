from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "index.html"


class _Audit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.scripts = []
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "script":
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None


def test_pages_demo_has_unique_controls_and_truthful_boundary():
    text = PAGE.read_text(encoding="utf-8")
    audit = _Audit()
    audit.feed(text)
    assert len(audit.ids) == len(set(audit.ids))
    assert {"replay", "queue", "architecture"}.issubset(audit.ids)
    assert "Zero blind submissions" in text
    assert "Only the owner may" in text
    assert "synthetic public demonstration" in text


def test_inline_demo_javascript_is_syntax_valid(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    text = PAGE.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", text, flags=re.S | re.I)
    assert len(scripts) == 1
    js = tmp_path / "demo.js"
    js.write_text(scripts[0], encoding="utf-8")
    result = subprocess.run([node, "--check", str(js)], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr
