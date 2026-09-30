import subprocess
import tempfile
import unittest


IMAGE = "local/steamgifts-qinglong:2.20.2"


class ContainerRuntimeTests(unittest.TestCase):
    def test_python_dependencies_survive_qinglong_data_mount(self):
        with tempfile.TemporaryDirectory() as data_dir:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--volume",
                    f"{data_dir}:/ql/data",
                    "--entrypoint",
                    "python3",
                    IMAGE,
                    "-c",
                    "import requests, bs4",
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
