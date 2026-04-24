"""
tests/test_docs_sanity.py — Sanity checks for Step 10 documentation artifacts.

Verifies that required documentation files exist and meet minimum size / content
expectations. These tests are intentionally lightweight — they catch accidental
deletion or an empty-file mistake, not content correctness.

Run with: pytest tests/test_docs_sanity.py -v
"""

import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestDocumentationArtifacts(unittest.TestCase):
    """Check that all Step 10 documentation files are present."""

    def _path(self, *parts):
        return os.path.join(REPO_ROOT, *parts)

    # ------------------------------------------------------------------
    # sparrow_elastic/README.md
    # ------------------------------------------------------------------

    def test_sparrow_elastic_readme_exists(self):
        path = self._path("sparrow_elastic", "README.md")
        self.assertTrue(
            os.path.isfile(path),
            f"sparrow_elastic/README.md not found at {path}",
        )

    def test_sparrow_elastic_readme_size(self):
        """README must be at least 3 KB (guards against stub or accidental truncation)."""
        path = self._path("sparrow_elastic", "README.md")
        size = os.path.getsize(path)
        self.assertGreater(
            size,
            3 * 1024,
            f"sparrow_elastic/README.md is only {size} bytes; expected > 3072",
        )

    def test_sparrow_elastic_readme_has_required_sections(self):
        """README must contain the key section headings an operator needs."""
        path = self._path("sparrow_elastic", "README.md")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        required_sections = [
            "Quickstart",
            "Engine Selection",
            "Auth Modes",
            "Bootstrap Behavior",
            "Index Structure",
            "Reference Data Refresh",
            "Fingerbank",
            "Device Classifier",
            "Data Flow",
            "Channel Model",
            "Dashboards",
            "Troubleshooting",
            "CLI Reference",
            "Files Layout",
        ]
        for section in required_sections:
            self.assertIn(
                section,
                content,
                f"README is missing section: {section!r}",
            )

    # ------------------------------------------------------------------
    # sparrow-elastic.conf.example
    # ------------------------------------------------------------------

    def test_conf_example_exists(self):
        path = self._path("sparrow-elastic.conf.example")
        self.assertTrue(
            os.path.isfile(path),
            f"sparrow-elastic.conf.example not found at {path}",
        )

    def test_conf_example_has_elastic_section(self):
        path = self._path("sparrow-elastic.conf.example")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("[elastic]", content, "conf.example missing [elastic] section")

    def test_conf_example_covers_key_settings(self):
        """Conf example should reference every documented settings key."""
        path = self._path("sparrow-elastic.conf.example")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        expected_keys = [
            "engine",
            "url",
            "username",
            "password",
            "api_key",
            "verify_certs",
            "poll_interval_sec",
            "flush_interval_sec",
            "batch_size",
            "fingerbank_api_key",
            "fingerbank_offline_db",
            "observer_id",
            "agent_host",
            "agent_port",
            "wifi_interface",
            "wifi_alias",
            "bt_alias",
        ]
        for key in expected_keys:
            self.assertIn(
                key,
                content,
                f"sparrow-elastic.conf.example does not mention key: {key!r}",
            )

    # ------------------------------------------------------------------
    # sparrow-elastic.env.example
    # ------------------------------------------------------------------

    def test_env_example_exists(self):
        path = self._path("sparrow-elastic.env.example")
        self.assertTrue(
            os.path.isfile(path),
            f"sparrow-elastic.env.example not found at {path}",
        )

    def test_env_example_has_sparrow_es_url(self):
        path = self._path("sparrow-elastic.env.example")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn(
            "SPARROW_ES_URL",
            content,
            "sparrow-elastic.env.example must define SPARROW_ES_URL",
        )

    # ------------------------------------------------------------------
    # init.d_scripts/sparrow-elastic.service.example
    # ------------------------------------------------------------------

    def test_service_example_exists(self):
        path = self._path("init.d_scripts", "sparrow-elastic.service.example")
        self.assertTrue(
            os.path.isfile(path),
            f"sparrow-elastic.service.example not found at {path}",
        )

    def test_service_example_is_valid_unit(self):
        """Unit file must have [Unit], [Service], and [Install] sections."""
        path = self._path("init.d_scripts", "sparrow-elastic.service.example")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        for section in ("[Unit]", "[Service]", "[Install]"):
            self.assertIn(
                section,
                content,
                f"service.example is missing {section}",
            )

    def test_service_example_references_env_file(self):
        path = self._path("init.d_scripts", "sparrow-elastic.service.example")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn(
            "EnvironmentFile",
            content,
            "service.example should reference an EnvironmentFile",
        )


if __name__ == "__main__":
    unittest.main()
