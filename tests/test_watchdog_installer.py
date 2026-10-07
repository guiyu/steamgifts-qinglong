import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "macos" / "com.guiyu.steamgifts-qinglong-watchdog.plist.template"
INSTALLER = ROOT / "scripts" / "install-watchdog.sh"
LABEL = "com.guiyu.steamgifts-qinglong-watchdog"


class WatchdogInstallerTests(unittest.TestCase):
    def test_template_defines_five_minute_run_at_load_agent_without_credentials(self):
        payload = plistlib.loads(TEMPLATE.read_bytes())
        text = TEMPLATE.read_text(encoding="utf-8").lower()

        self.assertEqual(payload["Label"], LABEL)
        self.assertEqual(payload["StartInterval"], 300)
        self.assertTrue(payload["RunAtLoad"])
        self.assertNotIn("phpsessid", text)
        self.assertNotIn("cf_clearance", text)
        self.assertNotIn("bearer", text)

    def test_dry_run_renders_absolute_paths_safely_without_real_launchagents(self):
        with tempfile.TemporaryDirectory(prefix="watchdog installer ") as temp_dir:
            temp = Path(temp_dir)
            project = temp / "Project With Spaces"
            destination = temp / "Test LaunchAgents"
            (project / "scripts").mkdir(parents=True)
            (project / "deploy" / "macos").mkdir(parents=True)
            shutil.copy(ROOT / "scripts" / "watchdog.py", project / "scripts")
            shutil.copy(INSTALLER, project / "scripts")
            shutil.copy(TEMPLATE, project / "deploy" / "macos")

            env = os.environ.copy()
            env["WATCHDOG_LAUNCH_AGENTS_DIR"] = str(destination)
            env["WATCHDOG_INSTALL_DRY_RUN"] = "1"
            env["WATCHDOG_DOCKER_BIN"] = "/usr/bin/false"
            result = subprocess.run(
                ["bash", str(project / "scripts" / "install-watchdog.sh")],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rendered = destination / f"{LABEL}.plist"
            self.assertTrue(rendered.exists())
            payload = plistlib.loads(rendered.read_bytes())
            arguments = payload["ProgramArguments"]
            resolved_project = project.resolve()
            self.assertEqual(arguments[0], "/usr/bin/python3")
            self.assertEqual(
                arguments[1], str(resolved_project / "scripts" / "watchdog.py")
            )
            self.assertEqual(arguments[2:], ["--project-dir", str(resolved_project)])
            self.assertEqual(
                payload["EnvironmentVariables"]["STEAMGIFTS_DOCKER_BIN"],
                "/usr/bin/false",
            )
            self.assertTrue(Path(payload["StandardOutPath"]).is_absolute())
            self.assertTrue(Path(payload["StandardErrorPath"]).is_absolute())
            self.assertTrue(
                (resolved_project / "data" / "watchdog" / "state.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
