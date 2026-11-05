"""Real subprocess coverage for ambient-module contamination during packaging."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_build_environment", ROOT / "tools/native_build_environment.py")
assert SPEC is not None and SPEC.loader is not None
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class NativeBuildEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.ambient = cls.root / "ambient"
        cls.ambient.mkdir()
        (cls.ambient / "pkg_resources.py").write_text("AMBIENT_ONLY = True\n", encoding="utf-8")
        (cls.ambient / "native_build_pollution.py").write_text("AMBIENT_ONLY = True\n", encoding="utf-8")
        metadata = cls.ambient / "setuptools-84.1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: setuptools\nVersion: 84.1.0\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(cls.ambient))
        env.pop("PYTHONHOME", None)
        probe = "from importlib.metadata import version; from importlib.util import find_spec; import json; print(json.dumps({'version':version('setuptools'),'origin':find_spec('pkg_resources').origin}))"
        cls.base = json.loads(subprocess.check_output([sys.executable, "-c", probe], cwd=cls.root, env=env, text=True, timeout=30))
        with patch.dict(os.environ, {"PYTHONPATH": str(cls.ambient), "PYTHONHOME": str(cls.root / "foreign-home"), "HOME": str(cls.root / "not-runtime"), "VIRTUAL_ENV": "/unrelated"}):
            cls.result = BUILD.create_environment(cls.root, tuple(sys.version_info[:3]))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_metadata_only_accepts_pollution_but_fresh_native_python_excludes_it(self) -> None:
        self.assertEqual(self.base["version"], "84.1.0")
        self.assertEqual(Path(self.base["origin"]), self.ambient / "pkg_resources.py")
        probe = """import importlib.util,json,sys
p=importlib.util.find_spec('pkg_resources')
print(json.dumps({'ambient':importlib.util.find_spec('native_build_pollution') is not None,
                 'pkg':None if p is None else p.origin,'prefix':sys.prefix}))
"""
        env = BUILD.clean_environment(self.result["directory"])
        observed = json.loads(subprocess.check_output([str(self.result["python"]), "-c", probe], cwd=self.result["directory"], env=env, text=True, timeout=30))
        self.assertFalse(observed["ambient"])
        self.assertEqual(Path(observed["prefix"]).resolve(), self.result["directory"])
        if observed["pkg"] is not None:
            self.assertTrue(Path(observed["pkg"]).resolve().is_relative_to(self.result["directory"]))
        self.assertNotEqual(observed["pkg"], str(self.ambient / "pkg_resources.py"))

    def test_version_mismatch_refuses_before_creating_any_environment(self) -> None:
        before = set(self.root.iterdir())
        with self.assertRaisesRegex(ValueError, "version differs"):
            BUILD.create_environment(self.root, (0, 0, 0))
        self.assertEqual(set(self.root.iterdir()), before)

    def test_published_bindings_use_fresh_exact_interpreter_and_clear_search_overrides(self) -> None:
        path_file, env_file = self.root / "github-path", self.root / "github-env"
        path_file.touch()
        env_file.touch()
        BUILD.publish_environment(self.result, self.root, path_file, env_file)
        self.assertEqual(path_file.read_text().splitlines(), [str(self.result["scripts"])])
        lines = env_file.read_text().splitlines()
        for expected in ["VIRTUAL_ENV=" + str(self.result["directory"]), "WEBJAM_BUILD_PYTHON=" + str(self.result["python"]), "PYTHONNOUSERSITE=1", "PYTHONPATH=", "PYTHONHOME="]:
            self.assertIn(expected, lines)

    def test_output_outside_runtime_is_rejected_before_either_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as foreign:
            path_file, env_file = self.root / "untouched-path", Path(foreign) / "env"
            path_file.touch()
            env_file.touch()
            with self.assertRaisesRegex(ValueError, "under RUNNER_TEMP"):
                BUILD.publish_environment(self.result, self.root, path_file, env_file)
            self.assertEqual(path_file.read_bytes(), b"")
            self.assertEqual(env_file.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
