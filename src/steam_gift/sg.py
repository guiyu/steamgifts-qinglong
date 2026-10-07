#!/usr/bin/python3
import time
from curl_cffi import requests as curl_requests
import random
import os
from bs4 import BeautifulSoup
import re
from subprocess import call
import configparser
from datetime import datetime, timezone
from pathlib import Path
from notify import send
from heartbeat import Heartbeat, write_heartbeat
from reviews import SteamReviewCatalog, extract_app_id
from selection import Candidate, is_qualified, select_candidates

requests = None
notify_push = ""

version = "1.4.9"


class AuthenticationBlocked(Exception):
    pass


class SiteError(Exception):
    pass

#determine was script installed by apt-package or was clonned via git clone


def check_new_version(ver):
    """Function for check new version of this script"""
    try:
        if requests.get("https://raw.githubusercontent.com/4815162342lost/steam_gifts_bot/master/version", timeout=120).text.rstrip() == ver:
            print(f"正在使用最新版本: {ver}")
        else:
            print(
                "有新版!\n https://github.com/4815162342lost/steam_gifts_bot")
            print("更新亮点:")
            print(
                requests.get("https://raw.githubusercontent.com/4815162342lost/steam_gifts_bot/master/whats_new", timeout=120).text)
    except:
        print("无法连接github")


def get_settings():
    """Function for read settings from settings.cfg file"""
    conf = configparser.ConfigParser()
    conf.optionxform=str
    conf.read('settings.cfg')
    return conf


def get_requests(cookie, req_type, headers):
    """Main function for raise other functions"""
    print(f"工作模式 {req_type} ")
    def do_requests(cookie, headers, start_link="https://www.steamgifts.com/giveaways/search?page=", end_link="", page_number = 1):
        ##Function for do get requests, decide does next page exist or not and raise next functions for enter to giveaways##
        while True:
            try:
                r = requests.get(f"{start_link}{page_number}{end_link}",cookies=cookie, headers=headers, timeout=120)
                get_game_links(r)
                if r.text.find("Next") == -1 or type(page_number) != int:
                    break
                page_number += 1
                time.sleep(random.randint(3, 14))
            except Exception as e:
                print("网站不可用")
                time.sleep(300)
                break
    if req_type == "wishlist" or req_type=="group" or req_type=="recommended":
        do_requests(cookie, headers, end_link=f"&type={req_type}")
    elif req_type == "search_list":
        for current_search in what_search:
            print(f"搜索包含以下内容的赠品: {current_search}")
            do_requests(cookie, headers, end_link=f"&q={current_search}")
            time.sleep(random.randint(8, 39))
    elif req_type == "random_list" and get_coins() > threshold:
        time.sleep(random.randint(5, 11))
        do_requests(cookie, headers, start_link="https://www.steamgifts.com/", page_number="")
    elif req_type == "enteredlist":
        print("尝试接收已找到的赠品...")
        entered_list = []
        page_number = 1
        while True:
            try:
                r = requests.get(f"https://www.steamgifts.com/giveaways/entered/search?page={page_number}", cookies=cookie, headers=headers, timeout=120)
                soup = BeautifulSoup(r.text, "html.parser")
                links = soup.find_all(class_="table__row-inner-wrap")
                if not links:
                    return entered_list
                for get_link in links:
                    url = get_link.find(class_="table__column__heading").get("href")
                    check_geaways_end = get_link.find(class_="table__remove-default is-clickable")
                    if check_geaways_end != None:
                        entered_list.append(url)
                    elif get_link.find(class_="table__column__deleted") != None:
                        continue
                    else:
                        return entered_list
                page_number += 1
                time.sleep(random.randint(3, 7))
            except Exception as e:
                print(f"由于异常，无法获取输入列表: {e}")
                time.sleep(300)
                return entered_list


def get_game_links(requests_result):
    """Collect review-backed giveaway candidates without entering them."""
    soup = BeautifulSoup(requests_result.text, "html.parser")
    link = soup.find_all(class_="giveaway__heading__name")
    for get_link in link:
        geaway_link = get_link.get("href")
        if not geaway_link or geaway_link in entered_url:
            continue
        if not need_giveaways_from_banners and geaway_link in giveaways_from_banner:
            continue

        full_url = "https://www.steamgifts.com" + geaway_link
        if full_url in bad_giveaways_link:
            print(f"赠品 URL 已列入黑名单: {full_url[full_url.rfind('/') + 1:]}")
            continue

        code_match = re.match(r"^/giveaway/([^/]+)/", geaway_link)
        if code_match and code_match.group(1) in candidate_by_code:
            continue
        row = (
            get_link.find_parent(class_="giveaway__row-outer-wrap")
            or get_link.find_parent(class_="giveaway__row-inner-wrap")
            or get_link.parent
        )
        points_match = re.search(r"\((\d+)P\)", row.get_text(" ", strip=True))
        steam_link = row.find(
            "a", href=re.compile(r"^https://store\.steampowered\.com/app/")
        )
        app_id = extract_app_id(steam_link.get("href") if steam_link else "")
        if not code_match or not points_match or app_id is None:
            print(f"跳过缺少 Steam App ID 或点数的赠品: {geaway_link}")
            continue

        review = review_catalog.get(app_id)
        if review is None:
            print(f"跳过没有可靠 Steam 评测数据的赠品: {geaway_link}")
            continue

        candidate = Candidate(
            code=code_match.group(1),
            url=full_url,
            points=int(points_match.group(1)),
            app_id=app_id,
            review=review,
        )
        candidate_by_code.setdefault(candidate.code, candidate)


def enter_qualified_candidates(candidates, budget):
    """Enter an optimal set and re-plan after every failed attempt."""
    remaining = {candidate.code: candidate for candidate in candidates}
    selected_count = 0
    entered_count = 0
    while remaining and budget > 0:
        selected = select_candidates(list(remaining.values()), budget)
        if not selected:
            break
        for candidate in selected:
            remaining.pop(candidate.code, None)
            selected_count += 1
            new_budget = enter_geaway(candidate.url)
            if new_budget is None:
                budget = get_coins()
                break
            budget = new_budget
            entered_count += 1
    return budget, selected_count, entered_count


def enter_geaway(geaway_link):
    """enter to giveaway"""
    bad_counter = good_counter = 0
    try:
        r = requests.get(geaway_link, cookies=cookie, headers=headers, timeout=120)
        if r.status_code != 200:
            set_notify("站点错误", f"错误代码: {r.status_code}", separator=". ")
            time.sleep(300)
            return None
    except:
        print("网站不可用")
        time.sleep(300)
        return None
    soup_enter = BeautifulSoup(r.text, "html.parser")
    for bad_word in forbidden_words:
        bad_counter += len(re.findall(bad_word, r.text, flags=re.IGNORECASE))
    if bad_counter > 0:
        for good_word in good_words:
            good_counter += len(re.findall(good_word, r.text, flags=re.IGNORECASE))
        if bad_counter > good_counter:
            print("这是个陷阱，网站检测到我是机器人!")
            with open("bad_giveaways.txt", "a") as bad_giveaways:
                bad_giveaways.write(geaway_link + "\n")
            return None
        if bad_counter == good_counter:
            set_notify("提示", f"成功打开赠品链接： {geaway_link}", separator="! ")
    try:
        game = soup_enter.title.string
    except:
        game = "Unknown game"
    if game in bad_games_name:
        print(f"游戏来自黑名单。忽略: {game} ")
        return None
    try:
        link = soup_enter.find(class_="sidebar").form
    except Exception as e:
        print(f"未知错误: {e}")
        return None
    if link is not None:
        link = link.find_all("input")
        params = {"xsrf_token": link[0].get("value"), "do": "entry_insert", "code": link[2].get("value")}
        try:
            r = requests.post("https://www.steamgifts.com/ajax.php", data=params, cookies=cookie, headers=headers, timeout=120)
            extract_coins = r.json()
        except:
            print("网站不可用...")
            time.sleep(300)
            return None
        if extract_coins["type"] == "success":
            coins = extract_coins["points"]
            set_notify("机器人参加了游戏赠品活动：", re.sub("&", '', game) + f"。 剩余硬币: {coins}", separator="")
            time.sleep(random.randint(1, 120))
            return int(coins)
        elif extract_coins.get("msg") == "Not Enough Points":
            print(f"没有足够的硬币参加 {geaway_link}")
            return None
    else:
        link = soup_enter.find(class_="sidebar__error is-disabled")
        if link is not None and link.get_text() == " Not Enough Points":
            print(f"没有足够的硬币参加 {geaway_link}")
            time.sleep(random.randint(5, 60))
            return None
        else:
            link = soup_enter.select("div.featured__column span")
            if link:
                print(f"赠品活动已结束。Bot 无法及时参与: {geaway_link}. 结束: {link[0].text}")
                time.sleep(random.randint(5, 60))
                return None
            else:
                set_notify("严重错误!", f"链接: {link}", separator="")
                return None
        return None


def get_coins():
    """How many coins do we have?"""
    try:
        soup = BeautifulSoup(requests.get("https://www.steamgifts.com/giveaways/search?type=wishlist", cookies=cookie, headers=headers, timeout=120).text, "html.parser")
        coins = int(soup.find(class_="nav__points").string)
        return coins
    except Exception as e:
        print(f"无法检索硬币数量... 异常: {e}")
        time.sleep(300)
        return 0


def set_notify(head, text, separator="\n"):
    """Set notify only on Linux. If non-Linux or you do want to receive notification just print it to console"""
    global notify_push
    print(head, text, sep=separator)
    notify_push = notify_push + head + separator + text + "\n"

def work_with_win_file(need_write, count):
    """Function for read drom file or write to file won.txt"""
    with open('won.txt', 'r+') as read_from_file:
        if not need_write:
            count = read_from_file.read()
            return count
        else:
            read_from_file.seek(0)
            read_from_file.write(str(count))

def check_won(count):
    """Check new won giveaway"""
    try:
        r = requests.get("https://www.steamgifts.com/giveaways/search?type=wishlist", cookies=cookie, headers=headers, timeout=120)
        soup = BeautifulSoup(r.text, "html.parser").find(class_="nav__right-container").find_all("a")[1].find(
            class_="nav__notification").string
    except:
        print("你一个赠品都没中。祝你下次好运!")
        work_with_win_file(True, 0)
        return 0
    if int(count) < int(soup):
        set_notify("恭喜您！您中奖了！", "在网站上领取奖品", separator=" ")
        work_with_win_file(True, soup)
        return soup
    elif int(count) > int(soup):
        work_with_win_file(True, soup)
        return soup
    return count




def get_games_from_banners():
    try:
        soup = BeautifulSoup(requests.get("https://www.steamgifts.com/", cookies=cookie, headers=headers, timeout=120).text,
                             "html.parser")
        banners = soup.find(class_="pinned-giveaways__inner-wrap pinned-giveaways__inner-wrap--minimized").find_all(
            class_="giveaway__heading__name")
        for games in banners:
            if games not in giveaways_from_banner:
                giveaways_from_banner.append(games.get("href"))
                print(f"你无法获得游戏 {games.get('href')}, 因为你拒绝参加横幅上的赠品活动......")
    except Exception as e:
        print(f"无法从横幅上获得游戏......例外：  {e}")



def run_bot(context):
    global bad_games_name, bad_giveaways_link, candidate_by_code, cookie
    global entered_url, forbidden_words, func_list, giveaways_from_banner
    global good_words, headers, min_positive_percent, min_review_count
    global need_giveaways_from_banners, notify_push, requests, review_catalog
    global threshold, what_search

    notify_push = ""
    requests = curl_requests.Session(impersonate="chrome150")
    set_notify("脚本启动", "——————————")
    time.sleep(60)

    settings = get_settings()
    cookie = {
        key: value
        for key, value in settings._sections["cookies"].items()
        if value.strip()
    }
    headers = dict(settings._sections["user-agent"])
    headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.7",
            "DNT": "1",
            "Priority": "u=0, i",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Sec-GPC": "1",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    need_giveaways_from_banners = int(
        settings["settings"]["giveaways_from_banners"]
    )
    threshold = int(settings["settings"]["threshold"])
    min_positive_percent = int(settings["settings"]["min_positive_percent"])
    min_review_count = int(settings["settings"]["min_review_count"])

    func_list = []
    for mode in ("wishlist", "search_list", "recommended", "group", "random_list"):
        if int(settings["settings"][mode]):
            func_list.append(mode)

    try:
        response = requests.get(
            "https://www.steamgifts.com/account/settings/profile",
            cookies=cookie,
            headers=headers,
            timeout=120,
        )
    except Exception as error:
        raise SiteError("profile_unavailable") from error

    response_text = response.text.lower()
    cloudflare_blocked = (
        response.status_code in (403, 429)
        or "cf-chl" in response_text
        or "just a moment" in response_text
    )
    profile_unavailable = "/account/settings/profile" not in response.url
    if cloudflare_blocked:
        set_notify(
            "Cookie 或访问状态无效",
            "Cloudflare 验证未通过；请先在浏览器完成验证并更新 Cookie",
        )
        raise AuthenticationBlocked("cloudflare_or_authentication")
    if response.status_code != 200 or profile_unavailable:
        set_notify("Cookie 已过期", "请更新您的 cookie")
        raise AuthenticationBlocked("cloudflare_or_authentication")
    set_notify("Cookies 没问题", "继续")

    with open("search.txt") as file:
        what_search = file.read().splitlines()
    with open("black_list_games_name.txt") as file:
        bad_games_name = file.read().splitlines()
    with open("bad_giveaways_link.txt") as file:
        bad_giveaways_link = file.read().splitlines()

    time.sleep(random.randint(2, 10))
    coins = get_coins()
    context["points_before"] = coins
    context["points_after"] = coins

    entered_url = get_requests(cookie, "enteredlist", headers) or []
    won_count = work_with_win_file(False, 0)
    set_notify("Steam gifts 脚本启动", f"硬币总量: {coins}")
    forbidden_words = (" ban", " fake", " bot", " not enter", " don't enter")
    good_words = (" bank", " banan", " both", " band", " banner", " bang", " bots?")
    giveaways_from_banner = []
    candidate_by_code = {}
    review_catalog = SteamReviewCatalog(requests)

    if not need_giveaways_from_banners:
        get_games_from_banners()
    for mode in func_list:
        get_requests(cookie, mode, headers)

    review_attempts = (
        review_catalog.successful_responses
        + review_catalog.invalid_responses
        + review_catalog.request_failures
    )
    if review_attempts and review_catalog.successful_responses == 0:
        raise SiteError("steam_review_unavailable")

    qualified_candidates = [
        candidate
        for candidate in candidate_by_code.values()
        if is_qualified(
            candidate.review,
            min_percent=min_positive_percent,
            min_reviews=min_review_count,
        )
    ]
    context["eligible_count"] = len(qualified_candidates)
    for candidate in candidate_by_code.values():
        if candidate not in qualified_candidates:
            print(f"跳过未达到评测门槛的赠品: {candidate.code}")

    coins, selected_count, entered_count = enter_qualified_candidates(
        qualified_candidates, coins
    )
    context["selected_count"] = selected_count
    context["entered_count"] = entered_count
    check_won(won_count)
    coins = get_coins()
    context["points_after"] = coins
    set_notify(
        "符合评测门槛的赠品处理完毕",
        f"本轮参加: {entered_count}，剩余硬币: {coins}",
    )
    set_notify("本轮任务已完成。", f"剩余硬币: {coins}", separator=" ")
    send("SteamGifts机器人", notify_push)

    if qualified_candidates:
        return "success", "completed"
    return "no_eligible_giveaways", "no_qualified_candidates"


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    heartbeat_path = Path(
        os.environ.get(
            "STEAMGIFTS_HEARTBEAT_PATH",
            "/ql/data/watchdog/bot-heartbeat.json",
        )
    )
    context = {
        "eligible_count": 0,
        "selected_count": 0,
        "entered_count": 0,
        "points_before": 0,
        "points_after": 0,
    }

    def finish(status, reason):
        write_heartbeat(
            heartbeat_path,
            Heartbeat(
                schema_version=1,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status=status,
                reason=reason,
                eligible_count=context["eligible_count"],
                selected_count=context["selected_count"],
                entered_count=context["entered_count"],
                points_before=context["points_before"],
                points_after=context["points_after"],
                process_id=os.getpid(),
            ),
        )

    try:
        status, reason = run_bot(context)
    except AuthenticationBlocked:
        finish("authentication_blocked", "cloudflare_or_authentication")
        return 1
    except SiteError as error:
        finish("site_error", str(error))
        return 1
    except Exception:
        finish("internal_error", "unexpected_failure")
        raise

    finish(status, reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
