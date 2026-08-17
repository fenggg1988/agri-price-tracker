# 🌱 农化价格监控器 (AgriChem Price Tracker)

每日抓取**化肥 / 农药**主要生产资料的市场价格，保存历史并生成走势图，监控市场方向。
参考 `gpu-price-tracker` 的 live + reference 架构，纯 Python、零密钥、可每日运行。

## 监控品种

| 类别 | 品种 | 数据来源 | 状态 |
|------|------|----------|------|
| 化肥 | 尿素 | 🟢 新浪期货 UR 主连（免费、无鉴权） | **实时** — 郑商所尿素期货，流动性最好，真实反映化肥方向 |
| 化肥 | 磷酸一铵 / 磷酸二铵 / 氯化钾 / 硫酸钾 | 🟢 生意社报价中心现货价 | **实时** — 真实浏览器渲染，取当日多家报价中位数 |
| 化肥 | 复合肥 | 🟡 参考价兜底 | 生意社对该页面设防，暂用参考价 |
| 农药 | 多菌灵 | 🟢 生意社报价中心现货价 | **实时** |
| 农药 | 草甘膦 / 阿维菌素 | 🟡 参考价兜底 | 生意社对该页面设防，暂用参考价 |
| 农药 | 吡虫啉 | 🟡 参考价兜底 | 生意社报价中心无此品种报价 |

> **数据来源说明**：农化现货（尤其农药）没有统一公开 API。本项目以「尿素期货实时价」锚定化肥方向，
> 其余品种用**真实浏览器（Playwright + 本机 Chrome / CI 上的 Chromium）**渲染生意社报价中心，
> 过其基础反爬后取当日多家报价的中位数。
> 生意社对部分高价值品种页（复合肥 / 草甘膦 / 阿维菌素）及缺失品种（吡虫啉）做了会员/反爬门槛，
> 这些品种自动回退到 `TARGETS` 中人工维护的参考价（看板中标红，并注明 `as_of` 核定日期）。
> 参考价请按 `as_of` 日期定期核对更新。

## 运行

```bash
pip install -r requirements.txt
# 生意社抓取依赖真实浏览器；本机已装 Chrome/Edge 会自动复用，否则执行：
playwright install chromium
python scrape_agri_prices.py
```

本地每日定时（Windows，需以管理员运行一次 PowerShell）：

```powershell
.\scheduler\setup_schedule.ps1        # 注册 每日 09:00 的计划任务
.\scheduler\setup_schedule.ps1 -Remove   # 取消
```

## 输出

- `data/agri_prices.json` — 按日期键值的完整历史（看板数据源）
- `data/agri_prices.csv` — 长表，便于 Excel / 二次分析（UTF-8-BOM）
- `agri_prices.html` — 自包含看板（内嵌数据 + Chart.js 画图，直接双击打开）
- `agri_prices.png` — 静态折线图（化肥线性轴 / 农药对数轴）
- `scrape.log` — 运行日志

## 自动化

`.github/workflows/daily.yml` 每日 **北京时间 09:00**（`0 1 * * *` UTC）在 GitHub Actions 运行，
抓取后自动 commit & push 数据文件，无需本机开机。手动触发：Actions 页面 → Run workflow。

## 维护参考价

打开 `scrape_agri_prices.py`，修改 `TARGETS` 中对应品种的 `ref_price` 与 `as_of`：

```python
{"key": "磷酸一铵", ..., "ref_price": 3300, "as_of": "2026-08-01", ...}
```

## 扩展数据源

`fetch_100ppi()` 是真实浏览器现货抓取层（Playwright）。若你有更稳的化肥/农药现货接口
（会员数据 API、期货交易所其他品种等），在 `collect()` 中增补抓取函数、在 `TARGETS` 增加品种（填 `pid` 即可接入生意社）
即可，无需改动持久化与画图逻辑。被生意社设防的品种可将 `pid` 置 `None` 直接走参考价。
