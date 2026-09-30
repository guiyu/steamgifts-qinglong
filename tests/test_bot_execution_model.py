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
    def run_bot_with_response(
        self,
        status_code=200,
        response_text="",
        use_real_notify=False,
        add_blank_clearance=False,
    ):
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
            if use_real_notify:
                shutil.copy(SOURCE / "notify.py", runtime / "notify.py")
            shutil.copy(SOURCE / "settings.cfg.example", runtime / "settings.cfg")
            if add_blank_clearance:
                config = configparser.ConfigParser()
                config.optionxform = str
                config.read(runtime / "settings.cfg")
                config["cookies"]["cf_clearance"] = ""
                with (runtime / "settings.cfg").open("w", encoding="utf-8") as file:
                    config.write(file)
            (runtime / "won.txt").write_text("0", encoding="utf-8")

            (fakes / "sitecustomize.py").write_text(
                "import time\ntime.sleep = lambda _seconds: None\n",
                encoding="utf-8",
            )
            (fakes / "requests.py").write_text(
                textwrap.dedent(
                    f"""
                    class Response:
                        status_code = {status_code}
                        text = {response_text!r}
                        url = "https://www.steamgifts.com/account/settings/profile"
                        history = []

                        def json(self):
                            return {{"type": "success", "points": 100}}


                    def get(*_args, **kwargs):
                        if any(value == "" for value in kwargs.get("cookies", {{}}).values()):
                            raise AssertionError("blank cookies must not be sent")
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
            if not use_real_notify:
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

            return result, count_file.read_text(encoding="utf-8")

    def test_script_runs_exactly_one_scan_and_exits(self):
        result, send_count = self.run_bot_with_response()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(send_count, "1")

    def test_cloudflare_challenge_fails_fast_without_running_scan(self):
        result, send_count = self.run_bot_with_response(
            status_code=403,
            response_text="<title>Just a moment...</title><script>cf-chl</script>",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cloudflare", result.stdout)
        self.assertEqual(send_count, "0")

    def test_console_summary_is_not_duplicated_by_notification_module(self):
        result, _send_count = self.run_bot_with_response(use_real_notify=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("本轮任务已完成。"), 1)

    def test_blank_optional_cookie_is_not_sent(self):
        result, _send_count = self.run_bot_with_response(add_blank_clearance=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_settings_example_contains_only_authentication_placeholders(self):
        config = configparser.ConfigParser()
        config.optionxform = str
        config.read(SOURCE / "settings.cfg.example")

        self.assertEqual(config["cookies"]["PHPSESSID"], "YOUR_PHPSESSID")
        self.assertEqual(config["cookies"]["cf_clearance"], "")
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
