# 🌱 农化·农产品价格监控器 (AgriChem & Fruit Price Tracker)

每日抓取**化肥 / 农药 / 水果**主要生产资料与农产品的市场价格，保存历史并生成走势图，监控市场方向。
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
| 水果 | 苹果 | 🟢 新浪期货 AP 主连（免费、无鉴权） | **实时** — 郑商所苹果期货，真实反映苹果方向 |
| 水果 | 苹果 | 🟢 新浪期货 AP 主连（免费、无鉴权） | **实时** — 郑商所苹果期货，真实反映苹果方向 |
| 水果 | 水果价格指数（官方） | 🟢 农业农村部公开接口（免费、无需登录） | **实时** — 农业农村部信息中心官方发布的"水果"价格指数（真实指数，非合成） |
| 水果 | 柑橘 / 梨 / 桃 / 李子 | ⚪ 暂不纳入 | 无免费公开单品源；农业农村部单品批发价明细接口需登录态 token（免费注册可取），未接入、不编造数据 |

> **水果数据源说明（坚持只抓真实数据、不合成指数）**：
> - **苹果**走郑商所期货主连（AP0），与尿素同款实时抓取。
> - **水果价格指数（官方）**来自农业农村部信息中心「全国农产品批发市场价格信息系统」的公开接口
>   （`/price_portal/pi-info-day/getIndexByLevel`，**免费、无需登录**），是官方真实发布的"水果"价格指数，非合成。
> - **柑橘 / 梨 / 桃 / 李子**：经实测，生意社报价中心只有化工品（无鲜果报价）；农业农村部按品种的批发价明细接口
>   （`/price_portal/web/portal-product/getPriceById`）需要登录态 token。该网站注册免费，但抓取需会话 token，
>   因此这 4 个单品**暂不纳入监控**，以免编造数据。若你注册并提供 token，可在 `fetch_moa_fruit_index()` 旁扩展单品抓取。

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
- `agri_prices.html` — 自包含看板（内嵌数据 + Chart.js 画图，直接双击打开；已同步生成 `index.html` 供 GitHub Pages）
- `agri_prices.png` — 静态折线图（化肥线性轴 / 农药对数轴 / 水果线性轴 / 指数轴）
- `scrape.log` — 运行日志

## 自动化

`.github/workflows/daily.yml` 每日 **北京时间 09:00**（`0 1 * * *` UTC）在 GitHub Actions 运行，
抓取后自动 commit & push 数据文件，无需本机开机。手动触发：Actions 页面 → Run workflow。
仓库已设为 **public**，并通过 **GitHub Pages** 公开看板：
`https://fenggg1988.github.io/agri-price-tracker/`

## 维护参考价 / 期货品种

打开 `scrape_agri_prices.py`：
- 改 `TARGETS` 中品种的 `ref_price` 与 `as_of`（参考价兜底项）；
- 水果已不再构造合成/代理指数；目前 苹果(期货) + 农业农村部官方水果价格指数 为真实数据源。
- 期货品种在 `TARGETS` 填 `"futures": "AP0"` 等主连代码即可接入新浪期货。

```python
{"key": "磷酸一铵", ..., "ref_price": 3300, "as_of": "2026-08-01", ...}
```

## 扩展数据源

`fetch_100ppi()` 是真实浏览器现货抓取层（Playwright）。若你有更稳的化肥/农药/水果现货接口
（会员数据 API、期货交易所其他品种等），在 `collect()` 中增补抓取函数、在 `TARGETS` 增加品种（填 `pid` 即可接入生意社）
即可，无需改动持久化与画图逻辑。被生意社设防的品种可将 `pid` 置 `None` 直接走参考价。
