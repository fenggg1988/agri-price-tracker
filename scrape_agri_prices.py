#!/usr/bin/env python3
"""
农化价格监控器 (AgriChem Price Tracker) — 每日时间序列采集器

监控三大类生产资料/农产品的市场方向：
  · 化肥 (Fertilizer) : 尿素、磷酸一铵、磷酸二铵、氯化钾、硫酸钾、复合肥
  · 农药 (Pesticide)  : 草甘膦、吡虫啉、多菌灵、阿维菌素
  · 水果 (Fruit)      : 苹果/柑橘类/梨/桃/李子 真实批发价(农业农村部全国重点农产品平台，免登录) + 水果价格指数(官方)

数据源策略（坚持"只抓真实数据、不合成指数"）：
  · 尿素/苹果 —— 实时抓取（新浪期货 UR0 / AP0 主连，免费、无鉴权、稳定），source="live"
  · 其余化肥/农药 —— 生意社报价中心现货价（Playwright 真实浏览器渲染，过基础反爬），
             取当日多家报价的中位数，source="live-100ppi"；
             单个品种不可达时回退到 TARGETS 中人工维护的参考价（source="reference"）。
  · 吡虫啉 —— 生意社报价中心暂无该品种报价，固定使用参考价兜底（已核实无数据源）。
  · 水果价格指数(官方) —— 农业农村部信息中心「全国农产品批发市场价格信息系统」公开接口
             （/price_portal/pi-info-day/getIndexByLevel，免费、无需登录），
             返回官方发布的"水果"价格指数，source="live-moa"。真实官方指数，非合成。
  · 水果单品批发价（苹果/柑橘类/梨/桃/李子）—— 农业农村部「全国重点农产品市场信息平台」
             (ncpscxx.moa.gov.cn) 免登录公开接口，返回全国各批发市场报价
             （AES-256-CBC 加密，已在脚本内解密），取中位数，单位 元/公斤，
             source="live-moa"。真实数据，非合成、非参考价。

输出：
  data/agri_prices.json  —— 按 YYYY-MM-DD 键值的完整历史
  data/agri_prices.csv   —— 长表格式，便于 Excel / 二次分析
  agri_prices.html       —— 自包含看板（内嵌数据 + Chart.js 画图，每次运行就地更新）
  agri_prices.png        —— 静态折线图（matplotlib，可选，缺依赖时自动跳过）
  scrape.log             —— 每次运行追加日志

可每日重复运行：同一天多次运行会覆盖当天记录，不会重复。
"""

import io
import os
import re
import csv
import json
import sys
import time
import statistics
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
import base64

# ── Windows UTF-8 修复 ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Playwright（真实浏览器渲染，用于过生意社基础反爬）。缺失时自动降级为参考价。
try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except Exception:
    HAVE_PW = False

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
OUTPUT_JSON = DATA_DIR / "agri_prices.json"
OUTPUT_CSV = DATA_DIR / "agri_prices.csv"
HTML_FILE = SCRIPT_DIR / "agri_prices.html"
PNG_FILE = SCRIPT_DIR / "agri_prices.png"
LOG_FILE = SCRIPT_DIR / "scrape.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
ISO_TIME = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ── 目标品种 ────────────────────────────────────────────────────────────────
# pid      : 生意社报价中心产品页 ID（mprice/plist-1-{pid}-1.html）；None=无实时源
# futures  : 新浪期货主连代码（UR0/AP0 等）；存在时优先走期货实时，失败回退参考价
# ref_price: 参考价（元/吨），仅当实时抓取不可达时兜底；as_of 为核定日期
TARGETS = [
    # 化肥
    {"key": "尿素",     "en": "Urea",         "category": "化肥", "unit": "元/吨",
     "pid": None, "futures": "UR0", "ref_price": 2200, "as_of": "2026-08-14"},
    {"key": "磷酸一铵", "en": "MAP",          "category": "化肥", "unit": "元/吨",
     "pid": 926,   "ref_price": 3350, "as_of": "2026-08-01"},
    {"key": "磷酸二铵", "en": "DAP",          "category": "化肥", "unit": "元/吨",
     "pid": 516,   "ref_price": 3750, "as_of": "2026-08-01"},
    {"key": "氯化钾",   "en": "KCl",          "category": "化肥", "unit": "元/吨",
     "pid": 927,   "ref_price": 2600, "as_of": "2026-08-01"},
    {"key": "硫酸钾",   "en": "SOP",          "category": "化肥", "unit": "元/吨",
     "pid": 1640,  "ref_price": 3500, "as_of": "2026-08-01"},
    {"key": "复合肥",   "en": "NPK",          "category": "化肥", "unit": "元/吨",
     "pid": 842,   "ref_price": 2800, "as_of": "2026-08-01"},
    # 农药
    {"key": "草甘膦",   "en": "Glyphosate",   "category": "农药", "unit": "元/吨",
     "pid": 1446,  "ref_price": 26000, "as_of": "2026-08-01"},
    {"key": "吡虫啉",   "en": "Imidacloprid", "category": "农药", "unit": "元/吨",
     "pid": None,  "ref_price": 75000, "as_of": "2026-08-01"},   # 生意社无该品种报价
    {"key": "多菌灵",   "en": "Carbendazim",  "category": "农药", "unit": "元/吨",
     "pid": 1453,  "ref_price": 35000, "as_of": "2026-08-01"},
    {"key": "阿维菌素", "en": "Abamectin",    "category": "农药", "unit": "元/吨",
     "pid": 1312,  "ref_price": 480000, "as_of": "2026-08-01"},
    # 水果（真实批发价：农业农村部「全国重点农产品市场信息平台」ncpscxx.moa.gov.cn
    #       免登录公开接口，返回全国各批发市场报价，AES 解密后取中位数；单位 元/公斤）
    #       vc = 品种编码（来自 /product/homeWholesaleProduct/selectTree 水果分支）
    {"key": "苹果",   "en": "Apple",  "category": "水果", "unit": "元/公斤", "vc": "AF01001",
     "ref_price": 8.0,  "as_of": "2026-08-01"},
    {"key": "柑橘类", "en": "Citrus", "category": "水果", "unit": "元/公斤", "vc": "AF05001",
     "ref_price": 6.0,  "as_of": "2026-08-01"},
    {"key": "梨",     "en": "Pear",   "category": "水果", "unit": "元/公斤", "vc": "AF01002",
     "ref_price": 5.0,  "as_of": "2026-08-01"},
    {"key": "桃",     "en": "Peach",  "category": "水果", "unit": "元/公斤", "vc": "AF03001",
     "ref_price": 8.0,  "as_of": "2026-08-01"},
    {"key": "李子",   "en": "Plum",   "category": "水果", "unit": "元/公斤", "vc": "AF03002",
     "ref_price": 5.0,  "as_of": "2026-08-01"},
]

# 注：不再构造任何合成/代理指数。水果价格只使用真实来源：苹果(新浪期货) + 农业农村部官方水果价格指数。


def log(msg: str) -> None:
    line = f"[{ISO_TIME}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except IOError:
        pass


# ── 实时抓取：新浪期货 尿素主连 (UR0) ──────────────────────────────────────

def fetch_urea_sina():
    """返回 (price, date) 或 None。UR0 = 郑商所尿素期货主连，元/吨。"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/json.php/"
           "InnerFuturesNewService.getDailyKLine?symbol=UR0")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    bars = r.json()
    if not bars:
        return None
    last = bars[-1]
    return float(last["c"]), last["d"]


def fetch_apple_sina():
    """返回 (price, date) 或 None。AP0 = 郑商所苹果期货主连，元/吨。"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/json.php/"
           "InnerFuturesNewService.getDailyKLine?symbol=AP0")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    bars = r.json()
    if not bars:
        return None
    last = bars[-1]
    return float(last["c"]), last["d"]


# ── 官方水果价格指数：农业农村部信息中心「全国农产品批发市场价格信息系统」 ──
# 公开接口，免费、无需登录鉴权；返回官方发布的"水果"价格指数（indexType=AF）。
MOA_INDEX_API = "https://pfsc.agri.cn/price_portal/pi-info-day/getIndexByLevel"

def fetch_moa_fruit_index():
    """返回 (value, publish_date) 或 None。农业农村部官方水果价格指数，真实、非合成。"""
    try:
        r = requests.post(
            MOA_INDEX_API,
            headers={**HEADERS, "Referer": "https://pfsc.agri.cn/"},
            json={}, timeout=20,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 200 or not d.get("content"):
            log("农业农村部指数接口返回异常: " + str(d.get("message", "")))
            return None
        for level in d["content"]:
            for row in level:
                if row.get("indexType") == "AF":   # AF = 水果
                    return float(row["indexValue"]), (row.get("publishDate") or "")[:10]
        return None
    except Exception as e:
        log(f"农业农村部水果指数抓取失败: {e}")
        return None


# ── 水果批发价：农业农村部「全国重点农产品市场信息平台」(ncpscxx.moa.gov.cn) ──
# 免登录公开接口：POST /product/homeWholesalePrice/selectWholesalePriceChart?varietyCode=xxx
# 返回 data 为 AES-256-CBC 密文（iv=前16字符, key 固定32字节, 密文=第17字符起 base64），
# 解密后 {"date":..., "x":[市场名], "y":[价格 元/公斤]}。取全国市场中位数作为当日价。
_NCPSXX_BASE = "https://ncpscxx.moa.gov.cn"
_AES_KEY = b"7s9K$pG2xQ8zR5mB7vA3sD9fH2jW40cV"   # 前端硬编码密钥（UTF-8, 32 字节）


def _decrypt_aes(data: str):
    """解密前端 CryptoJS AES.decrypt 结果（AES-256-CBC / PKCS7）。"""
    if not data:
        return None
    iv = data[:16].encode("utf-8")                      # 密文前 16 字符为 IV
    ct = base64.b64decode(data[16:])                    # 其后是 base64 密文
    decryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode("utf-8")


def fetch_ncpscxx_fruit(variety_code: str):
    """返回 (median, mean, n_markets, date) 或 None。农业农村部真实批发价（元/公斤）。"""
    try:
        r = requests.post(
            _NCPSXX_BASE + "/product/homeWholesalePrice/selectWholesalePriceChart",
            params={"varietyCode": variety_code},
            headers={**HEADERS, "Referer": _NCPSXX_BASE + "/queryDataMain/wholesalePrice"},
            timeout=20,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 0 or not d.get("data"):
            log(f"ncpscxx 水果 {variety_code}: 接口返回空")
            return None
        obj = json.loads(_decrypt_aes(d["data"]))
        ys = [float(v) for v in obj.get("y", []) if v not in (None, "")]
        if not ys:
            return None
        return (round(statistics.median(ys), 2), round(statistics.mean(ys), 2),
                len(ys), (obj.get("date") or "")[:10])
    except Exception as e:
        log(f"ncpscxx 水果 {variety_code} 抓取失败: {e}")
        return None


# ── 真实浏览器抓取：生意社报价中心现货价 ──────────────────────────────────

def _launch_browser(p):
    """优先复用本机已装 Chrome/Edge（避免下载 chromium），否则用 playwright 自带。"""
    candidates = []
    env = os.environ.get("CHROME_PATH")
    if env:
        candidates.append(env)
    candidates += [
        r"C:/Program Files/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            try:
                return p.chromium.launch(headless=True, executable_path=c,
                                         args=["--no-sandbox", "--disable-dev-shm-usage"])
            except Exception:
                continue
    # 回退：playwright 自带 chromium（需先 `playwright install chromium`）
    return p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])


def _parse_ppi_page(page, key):
    """解析报价表，返回 [(归一化到元/吨的价格, 单位文本, 日期), ...]"""
    rows = page.query_selector_all("table tr")
    out = []
    for tr in rows:
        tds = tr.query_selector_all("td")
        if len(tds) < 4:
            continue
        cells = [td.inner_text().strip() for td in tds]
        price_cell = date_cell = None
        for c in cells:
            if re.search(r"元/(吨|千克|kg|KG)", c):
                price_cell = c
            if re.match(r"\d{4}-\d{2}-\d{2}", c):
                date_cell = c
        if not price_cell or not date_cell:
            continue
        m = re.search(r"([\d,]+(?:\.\d+)?)\s*元/(吨|千克|kg|KG)", price_cell)
        if not m:
            continue
        val = float(m.group(1).replace(",", ""))
        unit = m.group(2).lower()
        if unit in ("千克", "kg"):   # 归一化到 元/吨
            val *= 1000
        out.append((val, "元/吨", date_cell))
    return out


def fetch_100ppi():
    """返回 {品种key: (price, unit, date)}；best-effort，失败品种不出现。"""
    if not HAVE_PW:
        log("Playwright 不可用，跳过生意社抓取（全程使用参考价）")
        return {}
    pids = {t["key"]: t["pid"] for t in TARGETS
            if t.get("pid") and t["key"] != "尿素"}
    if not pids:
        return {}
    result = {}
    try:
        with sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page()
            page.set_default_timeout(20000)
            for key, pid in pids.items():
                url = f"https://www.100ppi.com/mprice/plist-1-{pid}-1.html"
                quotes = []
                # 生意社对连续快速请求有限流/渲染时序问题，空结果则重试
                for attempt in range(3):
                    try:
                        page.goto(url, wait_until="networkidle", timeout=25000)
                    except Exception as e:
                        log(f"生意社 {key} 加载失败(第{attempt+1}次): {e}")
                        page.wait_for_timeout(3000)
                        continue
                    page.wait_for_timeout(2500)   # 等报价表 JS 渲染
                    quotes = _parse_ppi_page(page, key)
                    if quotes:
                        break
                    page.wait_for_timeout(4000)    # 空：可能限流，放慢再试
                if quotes:
                    price = round(statistics.median(q[0] for q in quotes), 2)
                    date = quotes[0][2]
                    result[key] = (price, "元/吨", date)
                    log(f"生意社 {key}: 中位 {price:,.2f} 元/吨 "
                        f"(样本{len(quotes)}, 日{date})")
                else:
                    log(f"生意社 {key}: 页面无报价（可能限流，已回退参考价）")
                page.wait_for_timeout(1500)   # 页面之间留间隔，降低限流概率
            browser.close()
    except Exception as e:
        log(f"生意社抓取异常: {e}")
    return result


# ── 采集 ───────────────────────────────────────────────────────────────────

def collect():
    records = []

    # 1) 期货实时（新浪主连：UR0 尿素）
    futures_res = {}
    for sym, fn in (("UR0", fetch_urea_sina),):
        try:
            res = fn()
            if res:
                futures_res[sym] = res
                log(f"新浪期货 {sym}: {res[0]} 元/吨 (行情日 {res[1]})")
            else:
                log(f"新浪期货 {sym}: 无数据")
        except Exception as e:
            log(f"新浪期货 {sym} 抓取失败: {e}")

    # 2) 生意社现货价（真实浏览器）
    spot = fetch_100ppi()

    for t in TARGETS:
        key = t["key"]
        # 水果批发价（ncpscxx 免登录真实数据，单位 元/公斤）
        if t.get("vc"):
            fr = fetch_ncpscxx_fruit(t["vc"])
            if fr:
                median, mean, n, idate = fr
                records.append({
                    "item": key, "en": t["en"], "category": t["category"],
                    "price": median, "unit": t["unit"],
                    "source": "live-moa",
                    "provider": f"农业农村部批发价(全国{n}市场中位/均{mean})",
                    "as_of": idate,
                    "scrape_time": ISO_TIME,
                })
                log(f"ncpscxx {key}: 中位 {median} 元/公斤 (全国{n}市场, 日均{mean}, {idate})")
            else:
                records.append({
                    "item": key, "en": t["en"], "category": t["category"],
                    "price": t["ref_price"], "unit": t["unit"],
                    "source": "reference", "as_of": t["as_of"],
                    "scrape_time": ISO_TIME,
                })
                log(f"ncpscxx {key}: 抓取失败，回退参考价 {t['ref_price']} 元/公斤")
            continue
        # 期货实时品种
        fut = t.get("futures")
        if fut and fut in futures_res:
            price, fdate = futures_res[fut]
            records.append({
                "item": key, "en": t["en"], "category": t["category"],
                "price": round(price, 2), "unit": t["unit"],
                "source": "live", "provider": f"新浪期货 {fut}",
                "scrape_time": ISO_TIME,
            })
            continue
        # 生意社现货
        if key in spot:
            price, unit, date = spot[key]
            records.append({
                "item": key, "en": t["en"], "category": t["category"],
                "price": price, "unit": unit,
                "source": "live-100ppi", "provider": f"生意社报价中心({date})",
                "scrape_time": ISO_TIME,
            })
        else:
            note = "暂无免费日度源" if t["category"] == "水果" else ""
            rec = {
                "item": key, "en": t["en"], "category": t["category"],
                "price": t["ref_price"], "unit": t["unit"],
                "source": "reference", "as_of": t["as_of"],
                "scrape_time": ISO_TIME,
            }
            if note:
                rec["note"] = note
            records.append(rec)

    # 3) 水果价格指数（农业农村部官方，免费公开接口，真实指数、非合成）
    moa = fetch_moa_fruit_index()
    if moa:
        val, idate = moa
        records.append({
            "item": "水果价格指数(官方)", "en": "FruitIdxMOA", "category": "水果",
            "price": round(val, 2), "unit": "指数",
            "source": "live-moa",
            "provider": "农业农村部·全国农产品批发市场价格信息系统",
            "as_of": idate,
            "scrape_time": ISO_TIME,
        })
        log(f"农业农村部水果价格指数: {val} (发布日 {idate})")
    else:
        log("农业农村部水果指数: 未取到（不影响其他品种）")
    return records


# ── 持久化 ────────────────────────────────────────────────────────────────

def load_existing():
    if OUTPUT_JSON.exists():
        try:
            with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            log("WARN: 现有 JSON 不可读，重新开始")
    return {"last_updated": None, "history": {}}


def save(data):
    data["last_updated"] = ISO_TIME
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"已保存 {OUTPUT_JSON.name}")
    write_csv(data)
    embed_in_html(data)
    make_png(data)


def write_csv(data):
    rows = []
    for date, recs in data["history"].items():
        for r in recs:
            rows.append({
                "date": date, "item": r["item"], "en": r.get("en", ""),
                "category": r["category"], "price": r["price"],
                "unit": r["unit"], "source": r["source"],
                "provider": r.get("provider", ""), "as_of": r.get("as_of", ""),
            })
    rows.sort(key=lambda x: (x["date"], x["item"]))
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "item", "en", "category",
                                          "price", "unit", "source",
                                          "provider", "as_of"])
        w.writeheader()
        w.writerows(rows)
    log(f"已写出 {OUTPUT_CSV.name} ({len(rows)} 行)")


def build_series(data):
    """按品种聚合时间序列，返回 {item: [(date, price), ...]}。"""
    series = {}
    for date in sorted(data["history"].keys()):
        for r in data["history"][date]:
            series.setdefault(r["item"], []).append((date, r["price"]))
    return series


def embed_in_html(data):
    if not HTML_FILE.exists():
        log(f"WARN: {HTML_FILE.name} 缺失，跳过内嵌")
        return
    html = HTML_FILE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False)
    pattern = re.compile(r"const\s+EMBEDDED_DATA\s*=\s*\{.*?\};", re.DOTALL)
    if not pattern.search(html):
        log(f"WARN: {HTML_FILE.name} 中未找到 EMBEDDED_DATA 标记")
        return
    new_html = pattern.sub(f"const EMBEDDED_DATA = {payload};", html, count=1)
    if new_html != html:
        HTML_FILE.write_text(new_html, encoding="utf-8")
        log(f"已更新 {HTML_FILE.name}")
        # 同时生成 index.html 作为 GitHub Pages 的首页入口
        idx = SCRIPT_DIR / "index.html"
        idx.write_text(new_html, encoding="utf-8")
        log(f"已生成 {idx.name}（GitHub Pages 入口）")


def make_png(data):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as _dt
    except Exception as e:
        log(f"WARN: matplotlib 不可用，跳过 PNG 生成: {e}")
        return

    series = build_series(data)
    cat_of, en_of = {}, {}
    for recs in data["history"].values():
        for r in recs:
            cat_of[r["item"]] = r["category"]
            en_of[r["item"]] = r.get("en", r["item"])

    # 价格类（元/吨）按类别分组；指数类（单位含"指数"）单列一图
    price_cats = {}
    index_series = []
    for item, pts in series.items():
        if "指数" in (cat_of.get(item, "")) or "指数" in str(r_unit(data, item)):
            index_series.append((item, pts))
        else:
            cat = cat_of.get(item, "其他")
            price_cats.setdefault(cat, []).append((item, pts))

    charts = []
    for cat, items in price_cats.items():
        if items:
            charts.append((cat, items, False))
    if index_series:
        charts.append(("指数", index_series, False))

    if not charts:
        log("WARN: 无可用序列，跳过 PNG 生成")
        return

    # matplotlib 默认字体不含中文，PNG 统一用英文标签（看板 HTML 用中文）。
    fig, axes = plt.subplots(len(charts), 1, figsize=(11, 4.2 * len(charts)),
                             squeeze=False)
    colors = plt.cm.tab10.colors
    for ax, (cat, items, _log) in zip(axes[:, 0], charts):
        for i, (item, pts) in enumerate(items):
            xs = [_dt.strptime(d, "%Y-%m-%d") for d, _ in pts]
            ys = [p for _, p in pts]
            ax.plot(xs, ys, marker="o", label=en_of.get(item, item),
                    color=colors[i % len(colors)])
        if cat == "水果":
            title, ylabel = "Fruit wholesale price (CNY/kg)", "CNY/kg"
        elif cat == "指数":
            title, ylabel = "Price index (MOA official)", "Index"
        else:
            title, ylabel = f"{cat} price (CNY/ton)", "CNY/ton"
        ax.set_title(title)
        ax.set_xlabel("Date")
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        if cat == "农药":
            ax.set_yscale("log")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        fig.autofmt_xdate()
    fig.suptitle(f"Agri Price Monitor · updated {TODAY}", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(PNG_FILE, dpi=110)
    plt.close(fig)
    log(f"已生成 {PNG_FILE.name}")


def r_unit(data, item):
    """辅助：取某品种最新记录的单位（用于 make_png 判断指数类）。"""
    for date in sorted(data["history"].keys(), reverse=True):
        for r in data["history"][date]:
            if r["item"] == item:
                return r.get("unit", "")
    return ""


# ── 主流程 ────────────────────────────────────────────────────────────────

def main():
    log("=== 农化价格监控器 开始运行 ===")
    if not HAVE_PW:
        log("提示: 未安装 playwright，生意社现货价将不可用（仅尿素实时 + 其余参考价）")
    try:
        records = collect()
    except Exception:
        log("FATAL during collect():\n" + traceback.format_exc())
        sys.exit(1)

    live_n = sum(1 for r in records if r["source"].startswith("live"))
    log(f"采集 {len(records)} 条记录，其中实时 {live_n} 条，参考 {len(records) - live_n} 条")

    data = load_existing()
    data["history"][TODAY] = records

    # 保留最近 365 天
    dates = sorted(data["history"].keys())
    while len(dates) > 365:
        data["history"].pop(dates.pop(0), None)

    save(data)

    print("\n=== 今日价格 ===")
    for r in records:
        tag = {"live": "实时", "live-100ppi": "现货", "reference": f"参考@{r.get('as_of','')}"}.get(r["source"], r["source"])
        print(f"  {r['category']:>3} {r['item']:<6} {r['price']:>12,.2f} {r['unit']}  [{tag}]")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback as _tb
        _msg = _tb.format_exc()
        try:
            with open(SCRIPT_DIR / "crash.log", "a", encoding="utf-8") as _f:
                _f.write(f"[{ISO_TIME}]\n{_msg}\n")
        except Exception:
            pass
        print(_msg)
        raise
