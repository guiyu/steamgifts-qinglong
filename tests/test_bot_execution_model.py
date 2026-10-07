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
        require_browser_client=False,
        response_url="https://www.steamgifts.com/account/settings/profile",
        return_heartbeat=False,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            runtime = temp / "runtime"
            fakes = temp / "fakes"
            runtime.mkdir()
            (fakes / "bs4").mkdir(parents=True)
            (fakes / "curl_cffi").mkdir(parents=True)

            for name in (
                "sg.py",
                "selection.py",
                "reviews.py",
                "heartbeat.py",
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
            http_fixture = textwrap.dedent(
                f"""
                    class Response:
                        status_code = {status_code}
                        text = {response_text!r}
                        url = {response_url!r}
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
            )
            (fakes / "http_fixture.py").write_text(
                http_fixture,
                encoding="utf-8",
            )
            (fakes / "requests.py").write_text(
                (
                    'raise AssertionError("plain requests must not be imported")\n'
                    if require_browser_client
                    else "from http_fixture import *\n"
                ),
                encoding="utf-8",
            )
            (fakes / "curl_cffi" / "__init__.py").write_text(
                "from . import requests\n",
                encoding="utf-8",
            )
            (fakes / "curl_cffi" / "requests.py").write_text(
                textwrap.dedent(
                    f"""
                    from http_fixture import get, head, post


                    class Session:
                        def __init__(self, impersonate=None):
                            if {require_browser_client!r} and impersonate != "chrome":
                                raise AssertionError("Chrome impersonation is required")

                        def get(self, *args, **kwargs):
                            return get(*args, **kwargs)

                        def head(self, *args, **kwargs):
                            return head(*args, **kwargs)

                        def post(self, *args, **kwargs):
                            return post(*args, **kwargs)
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
            heartbeat_path = temp / "bot-heartbeat.json"
            env["STEAMGIFTS_HEARTBEAT_PATH"] = str(heartbeat_path)
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

            if return_heartbeat:
                heartbeat = (
                    __import__("json").loads(heartbeat_path.read_text(encoding="utf-8"))
                    if heartbeat_path.exists()
                    else None
                )
                return result, count_file.read_text(encoding="utf-8"), heartbeat
            return result, count_file.read_text(encoding="utf-8")

    def run_quality_scenario(self, review_failure=False):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            runtime = temp / "runtime"
            fakes = temp / "fakes"
            runtime.mkdir()
            (fakes / "curl_cffi").mkdir(parents=True)

            for name in (
                "sg.py",
                "selection.py",
                "reviews.py",
                "heartbeat.py",
                "search.txt",
                "bad_giveaways_link.txt",
                "black_list_games_name.txt",
            ):
                shutil.copy(SOURCE / name, runtime / name)
            shutil.copy(SOURCE / "settings.cfg.example", runtime / "settings.cfg")
            config = configparser.ConfigParser()
            config.optionxform = str
            config.read(runtime / "settings.cfg")
            for mode in ("group", "recommended", "search_list", "random_list"):
                config["settings"][mode] = "0"
            config["settings"]["wishlist"] = "1"
            config["settings"]["min_positive_percent"] = "80"
            config["settings"]["min_review_count"] = "100"
            with (runtime / "settings.cfg").open("w", encoding="utf-8") as file:
                config.write(file)
            (runtime / "won.txt").write_text("0", encoding="utf-8")

            (fakes / "sitecustomize.py").write_text(
                "import time\ntime.sleep = lambda _seconds: None\n",
                encoding="utf-8",
            )
            (fakes / "notify.py").write_text(
                "def send(_title, _content):\n    return None\n",
                encoding="utf-8",
            )
            (fakes / "curl_cffi" / "__init__.py").write_text(
                "from . import requests\n",
                encoding="utf-8",
            )
            (fakes / "curl_cffi" / "requests.py").write_text(
                textwrap.dedent(
                    """
                    import os
                    from pathlib import Path


                    EVENTS = Path(os.environ["BOT_EVENTS"])
                    LISTING = """
                    + repr(
                        """
                        <html><body>
                          <div class="nav__points">400</div>
                          <div class="giveaway__row-outer-wrap">
                            <a class="giveaway__heading__name" href="/giveaway/good1/good-game">Good Game</a>
                            <span class="giveaway__heading__thin">(3P)</span>
                            <a href="https://store.steampowered.com/app/101?utm_source=SteamGifts">Steam</a>
                          </div>
                          <div class="giveaway__row-outer-wrap">
                            <a class="giveaway__heading__name" href="/giveaway/low01/low-game">Low Game</a>
                            <span class="giveaway__heading__thin">(3P)</span>
                            <a href="https://store.steampowered.com/app/202">Steam</a>
                          </div>
                        </body></html>
                        """
                    )
                    + """
                    REVIEW_FAILURE = __REVIEW_FAILURE__


                    def record(value):
                        with EVENTS.open("a", encoding="utf-8") as file:
                            file.write(value + "\\n")


                    class Response:
                        def __init__(self, text="", url="", payload=None, status_code=200):
                            self.text = text
                            self.url = url
                            self.payload = payload
                            self.status_code = status_code
                            self.history = []

                        def json(self):
                            return self.payload


                    class Session:
                        def __init__(self, impersonate=None):
                            self.impersonate = impersonate

                        def get(self, url, **_kwargs):
                            if "/appreviews/" in url:
                                app_id = int(url.rsplit("/", 1)[1])
                                record(f"review:{app_id}")
                                if REVIEW_FAILURE:
                                    raise RuntimeError("review service unavailable")
                                positive = 90 if app_id == 101 else 79
                                return Response(payload={
                                    "success": 1,
                                    "query_summary": {
                                        "total_positive": positive,
                                        "total_reviews": 100,
                                    },
                                })
                            if "/account/settings/profile" in url:
                                return Response(url=url)
                            if "/giveaways/entered/" in url:
                                return Response("<html></html>", url)
                            if "/giveaway/" in url:
                                code = url.split("/giveaway/", 1)[1].split("/", 1)[0]
                                return Response(
                                    f"<html><title>{code}</title><div class='sidebar'><form>"
                                    f"<input value='token'><input value='unused'><input value='{code}'>"
                                    "</form></div></html>",
                                    url,
                                )
                            return Response(LISTING, url)

                        def post(self, _url, data=None, **_kwargs):
                            record(f"post:{data['code']}")
                            return Response(payload={"type": "success", "points": 397})
                    """
                ).replace("__REVIEW_FAILURE__", repr(review_failure)),
                encoding="utf-8",
            )

            events_file = temp / "events"
            events_file.write_text("", encoding="utf-8")
            env = os.environ.copy()
            env["BOT_EVENTS"] = str(events_file)
            heartbeat_path = temp / "bot-heartbeat.json"
            env["STEAMGIFTS_HEARTBEAT_PATH"] = str(heartbeat_path)
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
            heartbeat = (
                __import__("json").loads(heartbeat_path.read_text(encoding="utf-8"))
                if heartbeat_path.exists()
                else None
            )
            return result, events_file.read_text(encoding="utf-8").splitlines(), heartbeat

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

    def test_uses_browser_impersonation_client(self):
        result, _send_count = self.run_bot_with_response(
            require_browser_client=True,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_settings_example_contains_only_authentication_placeholders(self):
        config = configparser.ConfigParser()
        config.optionxform = str
        config.read(SOURCE / "settings.cfg.example")

        self.assertEqual(config["cookies"]["PHPSESSID"], "YOUR_PHPSESSID")
        self.assertEqual(config["cookies"]["cf_clearance"], "")
        self.assertEqual(config["user-agent"]["user-agent"], "YOUR_USER_AGENT")

    def test_settings_enable_exact_quality_thresholds(self):
        config = configparser.ConfigParser()
        config.read(SOURCE / "settings.cfg.example")

        self.assertEqual(config.getint("settings", "min_positive_percent"), 80)
        self.assertEqual(config.getint("settings", "min_review_count"), 100)

    def test_collects_all_reviews_before_entering_only_qualified_game(self):
        result, events, _heartbeat = self.run_quality_scenario()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("review:101", events)
        self.assertIn("review:202", events)
        self.assertEqual([event for event in events if event.startswith("post:")], ["post:good1"])
        first_post = next(index for index, event in enumerate(events) if event.startswith("post:"))
        self.assertTrue(all(event.startswith("review:") for event in events[:first_post]))

    def test_writes_success_and_no_eligible_terminal_statuses(self):
        success, _events, success_heartbeat = self.run_quality_scenario()
        empty, _send_count, empty_heartbeat = self.run_bot_with_response(
            return_heartbeat=True
        )

        self.assertEqual(success.returncode, 0, success.stdout + success.stderr)
        self.assertEqual(success_heartbeat["status"], "success")
        self.assertEqual(empty.returncode, 0, empty.stdout + empty.stderr)
        self.assertEqual(empty_heartbeat["status"], "no_eligible_giveaways")

    def test_writes_authentication_and_review_site_failure_statuses(self):
        blocked, _send_count, blocked_heartbeat = self.run_bot_with_response(
            status_code=403,
            response_text="<title>Just a moment...</title><script>cf-chl</script>",
            return_heartbeat=True,
        )
        review_failure, events, review_heartbeat = self.run_quality_scenario(
            review_failure=True
        )

        self.assertNotEqual(blocked.returncode, 0)
        self.assertEqual(blocked_heartbeat["status"], "authentication_blocked")
        self.assertNotEqual(review_failure.returncode, 0)
        self.assertEqual(review_heartbeat["status"], "site_error")
        self.assertFalse(any(event.startswith("post:") for event in events))

    def test_unexpected_exception_writes_internal_error_and_returns_nonzero(self):
        result, _send_count, heartbeat = self.run_bot_with_response(
            response_url=None,
            return_heartbeat=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(heartbeat["status"], "internal_error")

    def test_source_compiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            py_compile.compile(
                str(SOURCE / "sg.py"),
                cfile=str(Path(temp_dir) / "sg.pyc"),
                doraise=True,
            )

    def test_import_is_side_effect_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fakes = temp / "fakes"
            (fakes / "curl_cffi").mkdir(parents=True)
            (fakes / "curl_cffi" / "__init__.py").write_text(
                "from . import requests\n", encoding="utf-8"
            )
            (fakes / "curl_cffi" / "requests.py").write_text(
                "class Session:\n    def __init__(self, *_args, **_kwargs):\n"
                "        raise AssertionError('session created during import')\n",
                encoding="utf-8",
            )
            (fakes / "notify.py").write_text(
                "def send(*_args, **_kwargs):\n    raise AssertionError('send during import')\n",
                encoding="utf-8",
            )
            heartbeat_path = temp / "heartbeat.json"
            env = os.environ.copy()
            env["STEAMGIFTS_HEARTBEAT_PATH"] = str(heartbeat_path)
            env["PYTHONPATH"] = os.pathsep.join((str(fakes), str(SOURCE)))

            result = subprocess.run(
                [sys.executable, "-c", "import sg"],
                cwd=temp,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(heartbeat_path.exists())


if __name__ == "__main__":
    unittest.main()
