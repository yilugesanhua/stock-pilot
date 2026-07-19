# Stock Pilot

[![CI](https://github.com/yilugesanhua/stock-pilot/actions/workflows/ci.yml/badge.svg)](https://github.com/yilugesanhua/stock-pilot/actions/workflows/ci.yml)
[![CodeQL](https://github.com/yilugesanhua/stock-pilot/actions/workflows/codeql.yml/badge.svg)](https://github.com/yilugesanhua/stock-pilot/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/yilugesanhua/stock-pilot)](https://github.com/yilugesanhua/stock-pilot/releases)
[![License](https://img.shields.io/github/license/yilugesanhua/stock-pilot)](LICENSE)

Stock Pilot 是一个只读的 Codex 美股分析 skill，默认分析未来 2-8 周。它从 SEC、Yahoo、Cboe、美国财政部、FRED 和新闻源收集当前数据，执行数据新鲜度门禁，并输出中文 Markdown 与 JSON 报告。

它不会访问账户仓位、计算仓位大小或自动交易。当核心数据无法证明为最新时，报告会返回 `BLOCK_ACTION`，并隐藏禁止发布的买卖点位。

## 功能

- SEC submissions、Company Facts 与财报证据核对
- Yahoo 日线技术指标和相对强弱
- 当前 Cboe VIX、美国国债收益率和 FRED 宏观历史
- Google News RSS 与可选社交来源交叉验证
- Bull / Base / Bear 情景、入场确认、止损和目标区间
- 每层数据时间戳、来源 URL 和质量门禁
- Longbridge 可选增强；认证失败不会阻断公共数据路径

## 环境要求

- Python 3.12 或 3.13
- [uv](https://docs.astral.sh/uv/)
- 一个包含真实邮箱或项目 URL 的 `SEC_USER_AGENT`
- `FRED_API_KEY` 可选；宏观适配器使用公共 CSV 降级路径
- 可选的 Agent Reach、Longbridge 和 reasoning skill，详见 [DEPENDENCIES.md](DEPENDENCIES.md)

## 安装

新用户可以从 [Releases](https://github.com/yilugesanhua/stock-pilot/releases) 下载对应版本的 `stock-pilot-vX.Y.Z.zip`，并用同一页面的 `checksums.txt` 验证 SHA-256。

也可以从源码安装：

```powershell
git clone https://github.com/yilugesanhua/stock-pilot.git
cd stock-pilot
uv sync --frozen --project skill/stock-pilot
./scripts/install.ps1
```

Linux/macOS 用户可以先安装运行环境，再将 `skill/stock-pilot` 和两个已捆绑的 Longbridge skill 复制到 `~/.codex/skills/`。

```powershell
$env:SEC_USER_AGENT = "stock-pilot/0.2.2 your-real-email@your-domain.com"
uv run --project skill/stock-pilot python skill/stock-pilot/scripts/stock_pilot.py doctor --output doctor.json
```

不要直接复制示例地址访问 SEC；必须替换为你的真实联系信息。`doctor` 会显示可选集成是否可用。

## 使用

在 Codex 中输入：

```text
$stock-pilot 分析 GOOGL 最新走势，给出详细买卖点位
```

也可以直接运行 CLI：

```powershell
uv run --project skill/stock-pilot python skill/stock-pilot/scripts/stock_pilot.py run --ticker GOOGL --horizon 2-8w --output runs/GOOGL/latest
```

主要输出：

- `report.md`：面向用户的中文报告
- `report.json`：结构化结论和点位
- `quality.json`：新鲜度门禁和阻断原因
- `filings.json`、`financials.json`、`technicals.json`、`macro.json`、`news-catalysts.json`：证据层

## 测试

```powershell
uv sync --frozen --project skill/stock-pilot
uv run --project skill/stock-pilot python tools/check_version.py
uv run --project skill/stock-pilot python tools/offline_smoke.py
uv run --project skill/stock-pilot python -m compileall -q skill/stock-pilot/scripts tools
uv run --project skill/stock-pilot python -m unittest discover -s skill/stock-pilot/tests -v
```

CI 不访问实时市场数据。实时数据连通性由本地 `doctor` 和自愿执行的 ticker smoke test 验证。

## 项目边界

本项目是研究软件，不构成投资建议。市场数据可能延迟、缺失或被提供商修改。使用者需要遵守 SEC、Yahoo、Google News、Cboe、FRED、美国财政部、Longbridge 和 Agent Reach 的条款及速率限制。

请勿提交 `.env`、密钥、Cookie、OAuth Token、分析运行结果、截图或专有二进制文件。

## 维护

- 贡献方式：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全问题：[SECURITY.md](SECURITY.md)
- 版本记录：[CHANGELOG.md](CHANGELOG.md)
- 第三方说明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- 数据新鲜度规则：[design/data-freshness-policy.md](design/data-freshness-policy.md)

## License

Stock Pilot 自有代码使用 [MIT License](LICENSE)。捆绑和外部依赖仍受各自许可证约束。
