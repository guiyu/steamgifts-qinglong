import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def compose_config(self):
        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_qinglong_is_local_only_and_persistent(self):
        service = self.compose_config()["services"]["qinglong"]

        self.assertEqual(service["image"], "local/steamgifts-qinglong:2.20.2")
        self.assertEqual(service["restart"], "unless-stopped")
        self.assertEqual(service["environment"]["TZ"], "Asia/Shanghai")
        self.assertEqual(
            service["ports"],
            [
                {
                    "mode": "ingress",
                    "target": 5700,
                    "published": "5700",
                    "protocol": "tcp",
                    "host_ip": "127.0.0.1",
                }
            ],
        )

        mounts = {volume["target"]: volume for volume in service["volumes"]}
        self.assertEqual(mounts["/ql/data"]["type"], "bind")
        self.assertTrue(mounts["/ql/data"]["source"].endswith("/data"))
        self.assertEqual(mounts["/ql/data/scripts/steam_gift"]["type"], "bind")
        self.assertTrue(
            mounts["/ql/data/scripts/steam_gift"]["source"].endswith(
                "/src/steam_gift"
            )
        )
        self.assertNotIn("/var/run/docker.sock", mounts)

    def test_runtime_state_is_ignored_by_git(self):
        ignored_paths = (
            "data/example",
            "src/steam_gift/settings.cfg",
            "src/steam_gift/won.txt",
            "src/steam_gift/bad_giveaways.txt",
            "src/steam_gift/__pycache__/sg.cpython-313.pyc",
        )

        for path in ignored_paths:
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "check-ignore", "--quiet", "--no-index", path],
                    cwd=ROOT,
                )
                self.assertEqual(result.returncode, 0, f"not ignored: {path}")


if __name__ == "__main__":
    unittest.main()
