# 部署文档

本仓库自带两个可直接执行的命令行入口，买家环境里不需要交付方介入，也不需要交付方服务器：

| 入口 | 用途 | 默认数据源 |
| --- | --- | --- |
| `python -m core.cli --batch <批次号>` | 模块一~四主流程（合同生成、批次回填、发票核对、发票/归档回填） | 仓库自带夹具 `fixtures/lingxing/` 与 `fixtures/invoices/`，离线回放 |
| `python -m prepublish.check --config <yaml>` | 模块四启动自检（白名单 / 本地模型 / 原件目录） | 只读配置文件，不发任何请求 |

## 1. 环境要求

| 项 | 主环境（模块一/二/三） | 模块四虚拟机 |
| --- | --- | --- |
| Python | 3.11+ | 3.11+ |
| 内存 | 2 GB 起 | 16 GB 起（本地 7B 级模型） |
| 出网 | 需访问领星与钉钉开放平台 | **只允许内网**，白名单仅私有 CIDR |
| 磁盘 | 1 GB | 500 GB（发票与支付原件按批次存） |
| 依赖 | 标准库 + PyYAML + pytest | 标准库 + PyYAML + pytest（无新增依赖） |

## 2. 主环境部署

```bash
git clone <repo> clawhunt-1364
cd clawhunt-1364
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest -q
```

最后一条预期退出码 `0`、末行 `124 passed`。

### 2.1 配置项

新建 `config/local.yaml`（不入库）：

```yaml
lingxing:
  app_key: <领星应用 app_key>
  app_secret: <领星应用 app_secret>
  base_url: https://openapi.lingxing.com
dingtalk:
  app_key: <钉钉应用 app_key>
  app_secret: <钉钉应用 app_secret>
  sheet_id: <报关批次台账的 sheetId>
  operator_id: <人工确认操作人的 userId>
rules_root: rules
```

环境变量优先于配置文件：`LINGXING_APP_KEY`、`LINGXING_APP_SECRET`、`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`CLAWHUNT_1364_RULES`。

### 2.2 命令行入口 `python -m core.cli`

```bash
.venv/Scripts/python.exe -m core.cli --batch B2026-0001
```

| 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--batch` | 是 | — | 批次号，例如 `B2026-0001`；不在回放数据里即失败 |
| `--fixtures` | 否 | `fixtures/lingxing` | 回放夹具目录，含 `batches.json` / `products.json` / `suppliers.json` |
| `--rules` | 否 | 仓库内 `rules/` | 规则包目录（七张 yaml） |
| `--invoices` | 否 | `fixtures/invoices` | 发票夹具目录，含发票台账 `ledger.json`（品类/票面数量/开票日期）与预览件 `<批次号>.json`；预览件缺失时该步记 0 张并说明 |

它做的事，与接真实开放平台时完全同一套代码路径：

1. `Rules` 加载七张规则表（校验 `version` 与 `effective_from`）；
2. `LingxingClient` + `FixtureTransport` 按分页契约回放批次 / 产品 / 供应商，走的是和真接口相同的 `fetch_all` 翻页逻辑；
3. **模块一**：批次明细生成采购合同 `build_contract`（合同品名=开票名=报关名），并跑 `requirement_issues` 清点缺失要件；
4. **模块二**：`BatchFiller.fill` 逐字段回填 `Ledger`（每格带 `SourceRef`），再把台账写进 `DingTalkSheet` 的内存投影，统计行数 / 列数 / 格数 / 待补清单；
5. **模块三**：该批次的发票预览件与合同逐字段比对，输出差异清单；
6. **模块四**：`Module4Filler` 按品类扣减发票额度（`InvoiceUsage.use()` → `core.archive.remaining()`），把后 7 列写进台账，并生成四类原件归档链接；
7. **模块一知识库**：`DeclareNameBook` 按报关名登记海关编码，同名复用不重复建条目；
8. **人工闸门**：调用 `HumanGate.require_human("tax_bureau_submit", ...)`，无确认时如实报告「被阻断」，绝不静默放行；有确认时审计行同时追加到 `logs/audit.log`（JSONL）。

退出码语义：

| 退出码 | 含义 |
| --- | --- |
| `0` | 跑通，stdout 是一份 JSON 摘要 |
| `1` | 批次不存在（stderr：`批次不存在：<批次号>（回放夹具共 N 个批次，例如 ...）`） |
| `2` | 规则包加载/解析失败、夹具目录缺失或不是合法 JSON、运行期数据错误（stderr 以 `规则包加载失败：` / `夹具加载失败：` / `运行失败：` 开头） |

stdout 只输出 JSON、不掺日志，便于直接 `| jq` 或喂给下游脚本；所有错误信息走 stderr。

接真实环境时只需把 `FixtureTransport` 换成开放平台的 `transport(resource, offset, size)` 实现，其余参数、流程与摘要字段都不变。

### 2.3 规则包上线流程

1. 改 `rules/*.yaml` 的业务值（例如把 `unit.yaml` 的 `套->个` 从 12 改成 10）；
2. 同时把该文件的 `version` 与 `effective_from` 改到新日期；
3. 跑 `pytest -q` 与 `scripts/sample50.py`，两者都过才允许上线；
4. 上线后旧版本留档在 `rules/archive/<version>/`。

## 3. 模块四独立虚拟机部署

镜像由仓库内的 `deploy/isolated/Dockerfile` 现场构建（`python:3.11-slim` + `pip install -r requirements.txt`，不拉外部包），compose 里是 `build:` 段而不是远端镜像：

```bash
cd deploy/isolated
docker compose build            # 用本仓库源码构建 clawhunt-1364-module4:latest
docker compose up -d
docker compose exec app python -m prepublish.check --config /etc/clawhunt/isolated.yaml
docker compose ps               # STATUS 里应出现 (healthy)：healthcheck 跑的就是这条自检
```

`prepublish.check` 既是容器启动命令，也是 healthcheck 探针：退出码非 0 即容器退出或不健康，编排层不会把流量放进来。

本机（不用 docker）直接跑同样一条：

```bash
.venv/Scripts/python.exe -m prepublish.check --config deploy/isolated/isolated.example.yaml
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `--config` | 是 | 自检配置 yaml，必须含 `egress_whitelist`（列表）、`model_base_url`、`archive_path` 三个键 |

`prepublish.check` 三项自检（判定函数全部来自 `core/preflight.py`，本包不复制任何判定逻辑）：

1. `egress_whitelist` 每一条都是私有网段（`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`127.0.0.1/32`），出现公网即拒绝；
2. `model_base_url` 指向回环或内网（`127.0.0.1`、`localhost`、`10.`、`192.168.`、`172.16`–`172.31`；`172.32` 以上按公网拒绝）；
3. `archive_path` 不在任何云同步目录下（OneDrive / Dropbox / Google Drive / iCloud / 坚果云 / 百度网盘）。

退出码语义：

| 退出码 | 含义 | 输出 |
| --- | --- | --- |
| `0` | 三项全过，允许启动 | stdout 末尾一行 `ok` |
| `2` | 至少一项不过，**必须阻断启动** | stdout 打印 `problems: N` 并逐条 `[i] 原因` |
| `1` | 配置文件缺失、不是合法 YAML 或缺少必需键 | stderr `配置加载失败：...` |

docker-compose 把该命令设为容器启动命令，退出码 `2` 时容器直接退出，模块四进程不会起。

## 4. 回滚

1. 规则包回滚：把 `rules/` 换回上一版目录，改 `effective_from` 为回滚当日，重启进程；
2. 代码回滚：`git revert <commit>` 后重跑 `pytest -q`；
3. 台账回滚：钉钉表格按行版本恢复（每次写入前 `DingTalkSheet.writes` 留了行级写入记录，可据此定位）；
4. 模块四回滚：`docker compose down` 后把源码 checkout 回上一个 tag 重新 `docker compose build`（镜像是本地 build，没有远端 tag 可切），原件目录与 `logs/audit.log` 不受影响。

## 5. 日志

`core.cli` 每次运行在 stdout 打印一份 JSON 摘要（批次号、行数、列数、格数、模块四耗用与剩余、差异条数、闸门状态），重定向即得日志：`.venv/Scripts/python.exe -m core.cli --batch B2026-0001 > logs/cli-$(date +%Y%m%d).json`。

审计行单独落盘到 `logs/audit.log`（JSONL，一行一条，字段 `action/operator/digest/at/batch_no`），目录由 `AuditLog` 自动创建，`logs/` 已在 `.gitignore` 中。落盘是尽力而为：目录只读等情况下闸门判定照常，失败原因记在 `AuditLog.write_failures`，`AuditLog.persisted` 为 `False`。

## 6. 不依赖交付方服务器

两个入口都只读本机文件：`core.cli` 读 `fixtures/` 与 `rules/`，`prepublish.check` 读一份 yaml。仓库里没有任何指向交付方主机、云函数或 SaaS 的地址，也没有回调上报。买家在自己的机器上 clone 完即可跑完「安装 → 测试 → 差异清单 → 主流程 → 自检」五步（见 README「买家自验清单」），全程不需要外网。
