import os
import platform
import time
import random
import re
from typing import List, Dict, Optional, Tuple

import requests
from seleniumbase import SB
from pyvirtualdisplay import Display

"""
批量登录 https://searcade.com （通过 userveria SSO）

流程：
  1) 打开 https://searcade.com/en/admin/servers/<SERVER_ID>
     - 未登录会被导向 searcade 的 “Welcome back” 页：输入 email -> 点击 Continue with email
  2) 跳转到 userveria SSO 页 -> 输入密码 -> 提交
  3) 跳回 searcade 的服务器控制台页，停留 4-6 秒
  4) 返回 searcade 首页 https://searcade.com/，停留 3-5 秒
  5) 点击退出按钮 / 或访问 /logout 路径退出

环境变量：
  - ACCOUNTS_BATCH（多行，英文逗号分隔，每行一套账号）
      格式：
        1) 不发 TG：email,password
        2) 发 TG  ：email,password,tg_bot_token,tg_chat_id
  - SEARCADE_SERVER_ID（可选）
      控制台 URL 里的 server_id，默认 6927；也可以在 ACCOUNTS_BATCH 里覆盖。

示例：
export ACCOUNTS_BATCH='a1@example.com,pass1
a2@example.com,pass2,123456:AAxxxxxx,123456789
'

备注：
  - searcade 登录页在未登录访问受保护页面时，会显示带 email 输入框和 “Continue with email” 按钮
    的 SSO 引导页。userveria 密码页的具体 HTML 无法提前 100% 确认，脚本对密码输入框与提交按钮
    使用了多套候选选择器去匹配（type=password / name=password / #password 等）。
"""

HOME_URL = "https://searcade.com/"
# 默认服务器 ID（可被环境变量或账号行覆盖）
DEFAULT_SERVER_ID = os.getenv("SEARCADE_SERVER_ID", "6927").strip() or "6927"
SERVER_URL_TPL = "https://searcade.com/en/admin/servers/{server_id}"
LOGIN_ENTRY_TPL = SERVER_URL_TPL  # 访问受保护页面会被导向登录流程

SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# ---------- 登录表单候选选择器 ----------
# searcade 邮箱输入 + “Continue with email”
EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[id="email"]',
    'input[autocomplete="email"]',
    'input[placeholder*="mail" i]',
]
CONTINUE_BTN_SELECTORS = [
    'button:contains("Continue with email")',
    'button:contains("Continue")',
    'button[type="submit"]',
    'input[type="submit"]',
]

# userveria 密码输入 + 登录按钮
PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
    'input[id="password"]',
    'input[autocomplete="current-password"]',
]
PASSWORD_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:contains("Log in")',
    'button:contains("Login")',
    'button:contains("Sign in")',
    'button:contains("Se connecter")',  # 法语以防万一
]

# 登出候选：优先直接访问 /logout；同时支持点击页面上的退出按钮
LOGOUT_LINK_SELECTORS = [
    'a[href$="/logout"]',
    'a[href*="/logout"]',
    'button:contains("Log out")',
    'button:contains("Logout")',
    'button:contains("Sign out")',
    'a:contains("Log out")',
    'a:contains("Logout")',
    'a:contains("Sign out")',
]
LOGOUT_URL_CANDIDATES = [
    "https://searcade.com/en/logout",
    "https://searcade.com/logout",
]


def mask_email_keep_domain(email: str) -> str:
    e = (email or "").strip()
    if "@" not in e:
        return "***"
    name, domain = e.split("@", 1)
    if len(name) <= 1:
        name_mask = name or "*"
    elif len(name) == 2:
        name_mask = name[0] + name[1]
    else:
        name_mask = name[0] + ("*" * (len(name) - 2)) + name[-1]
    return f"{name_mask}@{domain}"


def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        os.environ["DISPLAY"] = display.new_display_var
        print("🖥️ Xvfb 已启动")
        return display
    return None


def screenshot(sb, name: str):
    path = f"{SCREENSHOT_DIR}/{name}"
    try:
        sb.save_screenshot(path)
        print(f"📸 {path}")
    except Exception as e:
        print(f"⚠️ 截图失败 {path}: {e}")


def tg_send(text: str, token: Optional[str] = None, chat_id: Optional[str] = None):
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        ).raise_for_status()
    except Exception as e:
        print(f"⚠️ TG 发送失败：{e}")


def build_accounts_from_env() -> List[Dict[str, str]]:
    batch = (os.getenv("ACCOUNTS_BATCH") or "").strip()
    if not batch:
        raise RuntimeError("❌ 缺少环境变量：请设置 ACCOUNTS_BATCH（即使只有一个账号也用它）")

    accounts: List[Dict[str, str]] = []
    for idx, raw in enumerate(batch.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(",")]

        # 支持 2 / 3 / 4 / 5 列：
        #   email,password
        #   email,password,server_id
        #   email,password,tg_bot_token,tg_chat_id
        #   email,password,server_id,tg_bot_token,tg_chat_id
        if len(parts) not in (2, 3, 4, 5):
            raise RuntimeError(
                f"❌ ACCOUNTS_BATCH 第 {idx} 行格式不对（允许 email,password | "
                f"email,password,server_id | email,password,tg_bot_token,tg_chat_id | "
                f"email,password,server_id,tg_bot_token,tg_chat_id）：{raw!r}"
            )

        email, password = parts[0], parts[1]

        server_id = DEFAULT_SERVER_ID
        tg_token = ""
        tg_chat = ""

        if len(parts) == 3:
            # email,password,server_id
            server_id = parts[2] or DEFAULT_SERVER_ID
        elif len(parts) == 4:
            # email,password,tg_bot_token,tg_chat_id
            tg_token = parts[2]
            tg_chat = parts[3]
        elif len(parts) == 5:
            # email,password,server_id,tg_bot_token,tg_chat_id
            server_id = parts[2] or DEFAULT_SERVER_ID
            tg_token = parts[3]
            tg_chat = parts[4]

        if not email or not password:
            raise RuntimeError(f"❌ ACCOUNTS_BATCH 第 {idx} 行存在空字段：{raw!r}")

        accounts.append(
            {
                "email": email,
                "password": password,
                "server_id": server_id,
                "tg_token": tg_token,
                "tg_chat": tg_chat,
            }
        )

    if not accounts:
        raise RuntimeError("❌ ACCOUNTS_BATCH 里没有有效账号行（空行/注释行不算）")

    return accounts


# ---------- 通用辅助：尝试多组选择器 ----------
def _first_visible(sb, selectors: List[str], timeout_each: float = 1.5) -> Optional[str]:
    """返回第一个可见的选择器；全都不可见则返回 None。"""
    for sel in selectors:
        try:
            if sb.is_element_visible(sel):
                return sel
        except Exception:
            continue
    # 再用 wait 轮一轮（每个短等待）
    for sel in selectors:
        try:
            sb.wait_for_element_visible(sel, timeout=timeout_each)
            return sel
        except Exception:
            continue
    return None


def _has_cf_clearance(sb: SB) -> bool:
    try:
        cookies = sb.get_cookies()
        cf_clearance = next((c["value"] for c in cookies if c.get("name") == "cf_clearance"), None)
        print("🧩 cf_clearance:", "OK" if cf_clearance else "NONE")
        return bool(cf_clearance)
    except Exception:
        return False


def _try_click_captcha(sb: SB, stage: str):
    try:
        sb.uc_gui_click_captcha()
        time.sleep(3)
    except Exception as e:
        print(f"⚠️ captcha 点击异常（{stage}）：{e}")


def _current_url(sb: SB) -> str:
    try:
        return (sb.get_current_url() or "").strip()
    except Exception:
        return ""


def _is_on_server_page(sb: SB, server_id: str) -> bool:
    url = _current_url(sb).lower()
    return f"/admin/servers/{server_id}" in url


def _is_on_login_flow(sb: SB) -> bool:
    """判断当前是否还在登录流程（searcade 邮箱页 / userveria 密码页）。"""
    if _first_visible(sb, EMAIL_SELECTORS):
        return True
    if _first_visible(sb, PASSWORD_SELECTORS):
        return True
    url = _current_url(sb).lower()
    if "userveria" in url:
        return True
    return False


# ---------- 登录步骤 ----------
def _do_email_step(sb: SB, email: str) -> bool:
    """searcade 邮箱页：输入 email -> Continue with email。"""
    email_sel = _first_visible(sb, EMAIL_SELECTORS, timeout_each=3)
    if not email_sel:
        return False

    try:
        sb.clear(email_sel)
        sb.type(email_sel, email)
    except Exception:
        return False

    btn_sel = _first_visible(sb, CONTINUE_BTN_SELECTORS, timeout_each=2)
    try:
        if btn_sel:
            sb.click(btn_sel)
        else:
            # 兜底：回车提交
            sb.send_keys(email_sel, "\n")
    except Exception:
        return False

    time.sleep(3)
    return True


def _do_password_step(sb: SB, password: str) -> bool:
    """userveria 密码页：输入密码 -> 提交。"""
    pwd_sel = _first_visible(sb, PASSWORD_SELECTORS, timeout_each=20)
    if not pwd_sel:
        return False

    try:
        sb.clear(pwd_sel)
        sb.type(pwd_sel, password)
    except Exception:
        return False

    btn_sel = _first_visible(sb, PASSWORD_SUBMIT_SELECTORS, timeout_each=3)
    try:
        if btn_sel:
            sb.click(btn_sel)
        else:
            sb.send_keys(pwd_sel, "\n")
    except Exception:
        return False

    time.sleep(4)
    return True


def _logout(sb: SB) -> bool:
    """尝试退出：优先点击页面上的 logout 链接 / 按钮；失败则直接访问 /logout URL。"""
    sel = _first_visible(sb, LOGOUT_LINK_SELECTORS, timeout_each=2)
    if sel:
        try:
            sb.scroll_to(sel)
            time.sleep(0.3)
            sb.click(sel)
            time.sleep(3)
            # 退出后通常会出现邮箱输入框或跳回登录页
            if _first_visible(sb, EMAIL_SELECTORS, timeout_each=3):
                return True
            url_now = _current_url(sb).lower()
            if "login" in url_now or "logout" in url_now or "searcade.com" in url_now and "/admin/" not in url_now:
                # 兜底判定：不再在受保护的 /admin/ 下就算退出成功
                if "/admin/" not in url_now:
                    return True
        except Exception:
            pass

    # 直接访问 /logout
    for url in LOGOUT_URL_CANDIDATES:
        try:
            sb.open(url)
            time.sleep(3)
            if _first_visible(sb, EMAIL_SELECTORS, timeout_each=3):
                return True
            url_now = _current_url(sb).lower()
            if "/admin/" not in url_now:
                return True
        except Exception:
            continue

    return False


def login_then_flow_one_account(
    email: str, password: str, server_id: str
) -> Tuple[str, bool, str, Optional[str], bool]:
    """
    返回：
      (status, has_cf_clearance, current_url, server_id_used, logout_ok)

    status:
      - "OK"   登录成功（无论 logout 是否成功）
      - "FAIL" 登录失败
    """
    server_url = SERVER_URL_TPL.format(server_id=server_id)

    with SB(uc=True, locale="en", test=True) as sb:
        print("🚀 浏览器启动（UC Mode）")

        # 1) 直接访问受保护的服务器控制台页，触发登录流程
        sb.uc_open_with_reconnect(server_url, reconnect_time=5.0)
        time.sleep(2)

        _try_click_captcha(sb, "访问控制台前")

        # 2) 如果已经登录（cookie 未过期），直接就在 server 页
        if _is_on_server_page(sb, server_id):
            print("✅ 已处于登录状态，直接进入 server 页")
        else:
            # 3) searcade 邮箱页
            screenshot(sb, f"01_email_page_{int(time.time())}.png")
            if not _do_email_step(sb, email):
                screenshot(sb, f"email_step_failed_{int(time.time())}.png")
                return "FAIL", _has_cf_clearance(sb), _current_url(sb), server_id, False

            _try_click_captcha(sb, "邮箱提交后")

            # 4) userveria 密码页
            screenshot(sb, f"02_password_page_{int(time.time())}.png")
            if not _do_password_step(sb, password):
                screenshot(sb, f"password_step_failed_{int(time.time())}.png")
                return "FAIL", _has_cf_clearance(sb), _current_url(sb), server_id, False

            _try_click_captcha(sb, "密码提交后")

            # 5) 等待跳回 searcade 服务器控制台
            ok = False
            for _ in range(20):
                if _is_on_server_page(sb, server_id):
                    ok = True
                    break
                if not _is_on_login_flow(sb) and "searcade.com" in _current_url(sb).lower():
                    # 兜底：跳回了 searcade，但未在 server 页 -> 主动进 server 页
                    try:
                        sb.open(server_url)
                        time.sleep(3)
                        if _is_on_server_page(sb, server_id):
                            ok = True
                            break
                    except Exception:
                        pass
                time.sleep(1)

            if not ok:
                screenshot(sb, f"post_login_not_on_server_{int(time.time())}.png")
                return "FAIL", _has_cf_clearance(sb), _current_url(sb), server_id, False

        # 6) 服务器页停留 4-6 秒
        screenshot(sb, f"03_server_page_{int(time.time())}.png")
        stay1 = random.randint(4, 6)
        print(f"⏳ 服务器页停留 {stay1} 秒...")
        time.sleep(stay1)

        # 7) 返回首页
        try:
            print(f"↩️ 返回首页：{HOME_URL}")
            sb.open(HOME_URL)
            sb.wait_for_element_visible("body", timeout=30)
        except Exception:
            screenshot(sb, f"back_home_failed_{int(time.time())}.png")
            return "OK", _has_cf_clearance(sb), _current_url(sb), server_id, False

        stay2 = random.randint(3, 5)
        print(f"⏳ 首页停留 {stay2} 秒...")
        time.sleep(stay2)
        screenshot(sb, f"04_home_page_{int(time.time())}.png")

        # 8) 退出
        logout_ok = _logout(sb)
        screenshot(sb, f"05_after_logout_{int(time.time())}.png")

        has_cf = _has_cf_clearance(sb)
        return "OK", has_cf, _current_url(sb), server_id, logout_ok


def main():
    accounts = build_accounts_from_env()
    display = setup_xvfb()

    ok = 0
    fail = 0
    logout_ok_count = 0
    tg_dests = set()

    try:
        for i, acc in enumerate(accounts, start=1):
            email = acc["email"]
            password = acc["password"]
            server_id = acc.get("server_id") or DEFAULT_SERVER_ID
            tg_token = (acc.get("tg_token") or "").strip()
            tg_chat = (acc.get("tg_chat") or "").strip()
            if tg_token and tg_chat:
                tg_dests.add((tg_token, tg_chat))

            safe_email = mask_email_keep_domain(email)

            print("\n" + "=" * 70)
            print(f"👤 [{i}/{len(accounts)}] 账号：{safe_email}  server_id={server_id}")
            print("=" * 70)

            try:
                status, has_cf, url_now, server_id_used, logout_ok = login_then_flow_one_account(
                    email, password, server_id
                )

                if status == "OK":
                    ok += 1
                    if logout_ok:
                        logout_ok_count += 1
                    msg = (
                        f"✅ searcade 登录成功\n"
                        f"账号：{safe_email}\n"
                        f"server_id：{server_id_used}\n"
                        f"退出：{'✅ 成功' if logout_ok else '❌ 失败'}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}"
                    )
                else:
                    fail += 1
                    msg = (
                        f"❌ searcade 登录失败\n"
                        f"账号：{safe_email}\n"
                        f"server_id：{server_id_used}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}"
                    )

                print(msg)
                tg_send(msg, tg_token, tg_chat)

            except Exception as e:
                fail += 1
                msg = f"❌ searcade 脚本异常\n账号：{safe_email}\n错误：{e}"
                print(msg)
                tg_send(msg, tg_token, tg_chat)

            # 账号之间冷却
            time.sleep(5)
            if i < len(accounts):
                time.sleep(5)

        summary = f"📌 本次批量完成：登录成功 {ok} / 失败 {fail} | 退出成功 {logout_ok_count}/{ok}"
        print("\n" + summary)
        for token, chat in sorted(tg_dests):
            tg_send(summary, token, chat)

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
