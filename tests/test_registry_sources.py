"""Bundled source regressions with synthetic network responses."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_official_prompt_tailor import SKILL, ev


class TestModelGuideSources(unittest.TestCase):
    def test_gpt6_guidance_survives_latest_model_rollover(self):
        registry = json.loads((SKILL / "references" / "registry.json").read_text(encoding="utf-8"))
        source = next(s for s in registry["sources"] if s["source_id"] == "openai-gpt-6-prompting")
        rules = [r for r in registry["rules"] if r["source_id"] == source["source_id"]]
        historical_url = "https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md"
        historical = "# Using GPT-6\n\n## Initiative and follow-through\n\n" + "\n\n".join(
            rule["source_excerpt"] for rule in rules
        )
        latest = "# Using GPT-6.2\n\n## Task completion\n\n" + (
            "Synthetic guidance for a newer model with a different set of prompting recommendations.\n" * 10
        )

        def respond(url, accept=None):
            body = historical if url == historical_url else latest
            return 200, {"content-type": "text/markdown"}, body.encode("utf-8")

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ev, "http_get", respond), \
                mock.patch.object(ev, "check_host_public", return_value="mock"):
            run = Path(directory)
            entry = ev.fetch_live(source, run)
            self.assertEqual(entry["status"], "live", entry["error"])
            body = (run / entry["body_path"]).read_text(encoding="utf-8")
            for rule in rules:
                with self.subTest(rule=rule["rule_id"]):
                    self.assertIn(rule["source_excerpt"], body)
