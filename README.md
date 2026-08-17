# 🌱 农化价格监控器 (AgriChem Price Tracker)

每日抓取**化肥 / 农药**主要生产资料的市场价格，保存历史并生成走势图，监控市场方向。
参考 `gpu-price-tracker` 的 live + reference 架构，纯 Python、零密钥、可每日运行。

## 监控品种

| 类别 | 品种 | 数据来源 | 说明 |
|------|------|----------|------|
| 化肥 | 尿素 | 🟢 实时（新浪期货 UR 主连） | 郑商所尿素期货，流动性最好，真实反映化肥方向 |
| 化肥 | 磷酸一铵 / 二铵 / 氯化钾 / 硫酸钾 / 复合肥 | 🟡 现货 best-effort + 参考价兜底 | 优先生意社报价中心现货价，不可达时回退参考价 |
| 农药 | 草甘膦 / 吡虫啉 / 多菌灵 / 阿维菌素 | 🟡 现货 best-effort + 参考价兜底 | 同上 |

> **数据来源说明**：农化现货（尤其农药）没有统一公开 API。本项目以「尿素期货实时价」作为化肥方向的锚点；
> 其余品种优先抓取生意社报价中心现货价（best-effort，反爬时可能失败），失败则使用下方人工维护的参考价。
> 参考价请在 `scrape_agri_prices.py` 的 `TARGETS` 中按 `as_of` 日期定期核对更新。

## 运行

```bash
pip install -r requirements.txt
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

`fetch_100ppi_quotes()` 是 best-effort 现货抓取层。若你有稳定的化肥/农药现货接口（如会员数据 API、
期货交易所其他品种），在 `collect()` 中增补抓取函数并在 `TARGETS` 增加品种即可，无需改动持久化与画图逻辑。
