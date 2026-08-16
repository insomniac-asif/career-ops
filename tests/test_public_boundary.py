import ast
import json
from pathlib import Path
import re

import career_ops


ROOT = Path(__file__).resolve().parents[1]


def test_core_has_no_network_browser_email_or_submission_imports():
    source = (ROOT / "career_ops.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({
        "requests", "httpx", "urllib", "selenium", "playwright", "smtplib",
        "imaplib",
    })
    assert career_ops.AUTHORITY["submit_application"] == "owner_only"


def test_public_fixture_declares_itself_synthetic():
    payload = json.loads(
        (ROOT / "examples" / "leads.synthetic.json").read_text(encoding="utf-8"))
    assert payload["fixture"] == "synthetic"
    assert all(row["id"].startswith("synthetic-") for row in payload["leads"])


def test_tracked_text_contains_no_email_phone_or_private_state():
    ignored_parts = {".git", ".pytest_cache", "__pycache__", ".career-ops"}
    texts = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in ignored_parts for part in path.parts):
            continue
        if path.suffix.lower() in {".py", ".md", ".json", ".html", ".yml", ".toml"}:
            texts.append(path.read_text(encoding="utf-8"))
    blob = "\n".join(texts)
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", blob, re.I)
    assert not re.search(r"\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}", blob)
    private_path = "data" + "/abl/career"
    assert private_path not in blob.lower()
