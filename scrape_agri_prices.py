#!/usr/bin/env python3
"""
农化价格监控器 (AgriChem Price Tracker) — 每日时间序列采集器

监控两大类农化生产资料的市场方向：
  · 化肥 (Fertilizer) : 尿素、磷酸一铵、磷酸二铵、氯化钾、硫酸钾、复合肥
  · 农药 (Pesticide)  : 草甘膦、吡虫啉、多菌灵、阿维菌素

数据来源策略（对齐 gpu-price-tracker 的 live + reference 模式）：
  · 尿素 —— 实时抓取（新浪期货 UR 主连 UR0，免费、无鉴权、稳定），source="live"
  · 其余品种 —— 优先尝试生意社报价中心现货价（best-effort，可达时 source="live-100ppi"）；
               不可达时回退到 TARGETS 中人工维护的参考价（source="reference"，带 as_of 日期）。

输出：
  data/agri_prices.json  —— 按 YYYY-MM-DD 键值的完整历史
  data/agri_prices.csv   —— 长表格式，便于 Excel / 二次分析
  agri_prices.html       —— 自包含看板（内嵌数据 + Chart.js 画图，每次运行就地更新）
  agri_prices.png        —— 静态折线图（matplotlib，可选，缺依赖时自动跳过）
  scrape.log             —— 每次运行追加日志

可每日重复运行：同一天多次运行会覆盖当天记录，不会重复。
"""

import io
import csv
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Windows UTF-8 修复 ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
# price 为参考价（元/吨），仅当 live 抓取失败且生意社不可达时作为兜底；
# as_of 是该参考价的核定日期，请定期核对更新。
# aliases 用于从生意社报价中心文本中模糊匹配现货价。
TARGETS = [
    # 化肥
    {"key": "尿素",       "en": "Urea",        "category": "化肥", "unit": "元/吨",
     "ref_price": 1687, "as_of": "2026-08-14", "aliases": ["尿素"]},
    {"key": "磷酸一铵",   "en": "MAP",          "category": "化肥", "unit": "元/吨",
     "ref_price": 3300, "as_of": "2026-08-01", "aliases": ["磷酸一铵", "一铵", "MAP"]},
    {"key": "磷酸二铵",   "en": "DAP",          "category": "化肥", "unit": "元/吨",
     "ref_price": 3700, "as_of": "2026-08-01", "aliases": ["磷酸二铵", "二铵", "DAP"]},
    {"key": "氯化钾",     "en": "KCl",          "category": "化肥", "unit": "元/吨",
     "ref_price": 2500, "as_of": "2026-08-01", "aliases": ["氯化钾", "钾肥"]},
    {"key": "硫酸钾",     "en": "SOP",          "category": "化肥", "unit": "元/吨",
     "ref_price": 3200, "as_of": "2026-08-01", "aliases": ["硫酸钾"]},
    {"key": "复合肥",     "en": "NPK",          "category": "化肥", "unit": "元/吨",
     "ref_price": 2700, "as_of": "2026-08-01", "aliases": ["复合肥", "复合肥料", "NPK"]},
    # 农药
    {"key": "草甘膦",     "en": "Glyphosate",   "category": "农药", "unit": "元/吨",
     "ref_price": 26000, "as_of": "2026-08-01", "aliases": ["草甘膦", "glyphosate"]},
    {"key": "吡虫啉",     "en": "Imidacloprid", "category": "农药", "unit": "元/吨",
     "ref_price": 75000, "as_of": "2026-08-01", "aliases": ["吡虫啉"]},
    {"key": "多菌灵",     "en": "Carbendazim",  "category": "农药", "unit": "元/吨",
     "ref_price": 35000, "as_of": "2026-08-01", "aliases": ["多菌灵"]},
    {"key": "阿维菌素",   "en": "Abamectin",    "category": "农药", "unit": "元/吨",
     "ref_price": 480000, "as_of": "2026-08-01", "aliases": ["阿维菌素"]},
]


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


# ── best-effort 抓取：生意社报价中心现货价 ────────────────────────────────

def fetch_100ppi_quotes():
    """返回 {品种key: 现货价} 字典；不可达时返回空 dict。best-effort。"""
    from bs4 import BeautifulSoup
    try:
        s = requests.Session()
        s.get("https://www.100ppi.com/", headers=HEADERS, timeout=15)
        page = None
        for attempt in range(3):
            resp = s.get("https://www.100ppi.com/price/", headers=HEADERS, timeout=20)
            if resp.status_code == 200 and len(resp.content) > 5000:
                page = resp.text
                break
            time.sleep(2)
        if not page:
            log("生意社报价中心不可达（被反爬拦截或超时），回退参考价")
            return {}
        soup = BeautifulSoup(page, "lxml")
        # 拼接所有文本行，按品种名匹配
        text = soup.get_text("\n")
        found = {}
        for t in TARGETS:
            for alias in t["aliases"]:
                # 在该行寻找别名，取其后紧跟的数字作为价格
                idx = text.find(alias)
                if idx != -1:
                    tail = text[idx: idx + 120]
                    nums = __import__("re").findall(r"(\d{3,7}(?:\.\d+)?)", tail)
                    if nums:
                        found[t["key"]] = float(nums[0])
                        break
        if found:
            log(f"生意社现货价命中 {len(found)} 个品种: " +
                ", ".join(f"{k}={v}" for k, v in found.items()))
        else:
            log("生意社页面已加载但未匹配到目标品种，回退参考价")
        return found
    except Exception as e:
        log(f"生意社抓取异常: {e}")
        return {}


# ── 采集 ───────────────────────────────────────────────────────────────────

def collect():
    records = []

    # 1) 尿素：实时
    urea_live = None
    try:
        res = fetch_urea_sina()
        if res:
            urea_live, urea_date = res
            log(f"新浪期货 尿素UR0: {urea_live} 元/吨 (行情日 {urea_date})")
        else:
            log("新浪期货 尿素UR0: 无数据")
    except Exception as e:
        log(f"新浪期货 尿素UR0 抓取失败: {e}")

    # 2) 生意社现货价（best-effort）
    spot = fetch_100ppi_quotes()

    for t in TARGETS:
        key = t["key"]
        if key == "尿素" and urea_live is not None:
            records.append({
                "item": key, "en": t["en"], "category": t["category"],
                "price": round(urea_live, 2), "unit": t["unit"],
                "source": "live", "provider": "新浪期货 UR0",
                "scrape_time": ISO_TIME,
            })
            continue
        # 优先生意社现货价
        if key in spot:
            records.append({
                "item": key, "en": t["en"], "category": t["category"],
                "price": round(spot[key], 2), "unit": t["unit"],
                "source": "live-100ppi", "provider": "生意社报价中心",
                "scrape_time": ISO_TIME,
            })
        else:
            records.append({
                "item": key, "en": t["en"], "category": t["category"],
                "price": t["ref_price"], "unit": t["unit"],
                "source": "reference", "as_of": t["as_of"],
                "scrape_time": ISO_TIME,
            })
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
    pattern = __import__("re").compile(r"const\s+EMBEDDED_DATA\s*=\s*\{.*?\};", __import__("re").DOTALL)
    if not pattern.search(html):
        log(f"WARN: {HTML_FILE.name} 中未找到 EMBEDDED_DATA 标记")
        return
    new_html = pattern.sub(f"const EMBEDDED_DATA = {payload};", html, count=1)
    if new_html != html:
        HTML_FILE.write_text(new_html, encoding="utf-8")
        log(f"已更新 {HTML_FILE.name}")


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
    # 重建 category / 英文名 映射（从 history 反查）
    cat_of, en_of = {}, {}
    for recs in data["history"].values():
        for r in recs:
            cat_of[r["item"]] = r["category"]
            en_of[r["item"]] = r.get("en", r["item"])
    cats = {"化肥": [], "农药": []}
    for item, pts in series.items():
        cat = cat_of.get(item, "化肥")
        if cat in cats:
            cats[cat].append((item, pts))

    # 注：matplotlib 默认字体不含中文，PNG 统一用英文标签（看板 HTML 用中文）。
    fig, axes = plt.subplots(2, 1, figsize=(11, 9))
    colors = plt.cm.tab10.colors
    for ax, (cat, items) in zip(axes, cats.items()):
        for i, (item, pts) in enumerate(items):
            xs = [_dt.strptime(d, "%Y-%m-%d") for d, _ in pts]
            ys = [p for _, p in pts]
            ax.plot(xs, ys, marker="o", label=en_of.get(item, item), color=colors[i % len(colors)])
        title = "Fertilizer price (CNY/ton)" if cat == "化肥" else "Pesticide price (CNY/ton, log scale)"
        ax.set_title(title)
        ax.set_xlabel("Date")
        ax.set_ylabel("Price")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        if cat == "农药":
            ax.set_yscale("log")  # 农药品种价差大，用对数轴
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        fig.autofmt_xdate()
    fig.suptitle(f"AgriChem Price Monitor · updated {TODAY}", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(PNG_FILE, dpi=110)
    plt.close(fig)
    log(f"已生成 {PNG_FILE.name}")


# ── 主流程 ────────────────────────────────────────────────────────────────

def main():
    log("=== 农化价格监控器 开始运行 ===")
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
        print(f"  {r['category']:>3} {r['item']:<6} {r['price']:>10,.2f} {r['unit']}  [{tag}]")


if __name__ == "__main__":
    main()
