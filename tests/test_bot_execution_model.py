import configparser
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "steam_gift"


class BotExecutionModelTests(unittest.TestCase):
    def test_script_runs_exactly_one_scan_and_exits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            runtime = temp / "runtime"
            fakes = temp / "fakes"
            runtime.mkdir()
            (fakes / "bs4").mkdir(parents=True)

            for name in (
                "sg.py",
                "search.txt",
                "bad_giveaways_link.txt",
                "black_list_games_name.txt",
            ):
                shutil.copy(SOURCE / name, runtime / name)
            shutil.copy(SOURCE / "settings.cfg.example", runtime / "settings.cfg")
            (runtime / "won.txt").write_text("0", encoding="utf-8")

            (fakes / "sitecustomize.py").write_text(
                "import time\ntime.sleep = lambda _seconds: None\n",
                encoding="utf-8",
            )
            (fakes / "requests.py").write_text(
                textwrap.dedent(
                    """
                    class Response:
                        status_code = 200
                        text = ""

                        def json(self):
                            return {"type": "success", "points": 100}


                    def get(*_args, **_kwargs):
                        return Response()


                    def head(*_args, **_kwargs):
                        return Response()


                    def post(*_args, **_kwargs):
                        raise AssertionError("fixture has no eligible giveaways")
                    """
                ),
                encoding="utf-8",
            )
            (fakes / "bs4" / "__init__.py").write_text(
                textwrap.dedent(
                    """
                    class Node:
                        def __init__(self, string=None):
                            self.string = string


                    class Soup:
                        title = Node("Fixture giveaway")

                        def find(self, class_=None):
                            if class_ == "nav__points":
                                return Node("100")
                            return None

                        def find_all(self, *_args, **_kwargs):
                            return []

                        def select(self, *_args, **_kwargs):
                            return []


                    def BeautifulSoup(*_args, **_kwargs):
                        return Soup()
                    """
                ),
                encoding="utf-8",
            )
            (fakes / "notify.py").write_text(
                textwrap.dedent(
                    """
                    import os
                    from pathlib import Path


                    def send(_title, _content):
                        path = Path(os.environ["BOT_SEND_COUNT"])
                        count = int(path.read_text() or "0") + 1
                        path.write_text(str(count))
                        if count > 1:
                            raise RuntimeError("bot started a second scan")
                    """
                ),
                encoding="utf-8",
            )

            count_file = temp / "send-count"
            count_file.write_text("0", encoding="utf-8")
            env = os.environ.copy()
            env["BOT_SEND_COUNT"] = str(count_file)
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(fakes), env.get("PYTHONPATH")))
            )

            result = subprocess.run(
                [sys.executable, "sg.py"],
                cwd=runtime,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(count_file.read_text(encoding="utf-8"), "1")

    def test_settings_example_contains_only_authentication_placeholders(self):
        config = configparser.ConfigParser()
        config.optionxform = str
        config.read(SOURCE / "settings.cfg.example")

        self.assertEqual(config["cookies"]["PHPSESSID"], "YOUR_PHPSESSID")
        self.assertEqual(config["user-agent"]["user-agent"], "YOUR_USER_AGENT")

    def test_source_compiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            py_compile.compile(
                str(SOURCE / "sg.py"),
                cfile=str(Path(temp_dir) / "sg.pyc"),
                doraise=True,
            )


if __name__ == "__main__":
    unittest.main()
