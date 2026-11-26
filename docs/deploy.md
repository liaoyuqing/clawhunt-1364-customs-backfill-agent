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

最后一条预期退出码 `0`、末行 `206 passed`。

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

## 2.4 接入真实平台（申请授权 → 配环境变量 → 冒烟 → 写台账）

四条外部连接都不在仓库里存地址与凭据，全部从环境变量读。**不配任何变量时，命令行为与纯离线自验完全一致。**

### 步骤 1：申请授权

| 平台 | 申请什么 | 拿到什么 |
| --- | --- | --- |
| 领星 ERP | 开放平台应用 | `app_key` / `app_secret`（或一个可用的 `access_token`）、所需资源调用权限 |
| 钉钉 | 企业内部应用 | `app_key` / `app_secret`、目标表格的 `sheet_id`、对该文档的读写权限 |
| 视觉模型 | OpenAI 兼容服务地址 | `base_url`、`api_key`（内网自建服务可留空） |
| 模块四本地模型 | 单独虚拟机上的本地 OpenAI 兼容服务 | `base_url`（必须回环或内网）、`model` 名 |
| 电子税务局 | 按当地对接方式 | `base_url`、`token` 或 `app_key`/`app_secret` |

### 步骤 2：配环境变量（不要写进仓库）

| 变量 | 用途 |
| --- | --- |
| `LINGXING_BASE_URL` / `LINGXING_APP_KEY` / `LINGXING_APP_SECRET` / `LINGXING_ACCESS_TOKEN` | 领星开放 API |
| `LINGXING_PAGE_SIZE` | 领星分页大小，默认 100 |
| `DINGTALK_BASE_URL` / `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` / `DINGTALK_SHEET_ID` | 钉钉开放平台 |
| `DINGTALK_TOKEN_PATH` / `DINGTALK_WRITE_PATH` / `DINGTALK_LIST_PATH` | 钉钉路由覆盖（不同版本可用不同路径） |
| `VISION_BASE_URL` / `VISION_API_KEY` / `VISION_MODEL` / `VISION_THRESHOLD` | 视觉模型；阈值默认 0.85 |
| `COMPUTER_USE_BASE_URL` / `COMPUTER_USE_API_KEY` / `COMPUTER_USE_MODEL` | 界面操作执行器 |
| `LOCAL_MODEL_BASE_URL` / `LOCAL_MODEL_API_KEY` / `LOCAL_MODEL_MODEL` | 模块四本地模型（地址必须回环或内网） |
| `ETAX_BASE_URL` / `ETAX_APP_KEY` / `ETAX_APP_SECRET` / `ETAX_TOKEN` / `ETAX_SUBMIT_PATH` | 电子税务局 |
| `CLAWHUNT_KNOWLEDGE_DB` | 品名知识库落盘路径，默认 `~/.clawhunt-1364/knowledge.json`（仓库外） |

### 步骤 3：冒烟（拉 1 个批次）

```bash
# 先用本地合成响应验证调用路径与解析逻辑
.venv/Scripts/python.exe scripts/recorded_smoke.py --source recorded

# 换真实账号后打真实接口（凭据上一步已配好）
.venv/Scripts/python.exe scripts/recorded_smoke.py --source live
```

冒烟会打印实际发出的请求（方法 / URL / 鉴权头名，值已脱敏 / body 键名）与平台应答；能取到商品报关八字段、能写回一行，才算接通。

### 步骤 4：跑一个批次并写回台账

```bash
# 领星真实取数（不写台账）
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 --source lingxing

# 台账直连钉钉：读现有行 + 写回本次回填结果
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 --source dingtalk --write-sheet
```

真实调用路径的开关（都需要先配好对应环境变量，否则命令会以退出码 2 明确报缺哪个变量）：

```bash
# 视觉补位 + 界面操作（图片地址模板支持 {batch_no} 与 {row_key}）
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 \
  --vision-image "http://127.0.0.1:9000/shot-{row_key}.png" --computer-use

# 模块四本地模型识别发票原件（地址必须过内网白名单）
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 \
  --local-model-image "http://127.0.0.1:8000/invoice-{row_key}.png" \
  --egress-whitelist "10.20.0.0/16,192.168.10.0/24"

# 电子税务局草稿 + 人工确认后提交
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 --etax
```

摘要里对应四段：`module2_vision`（调用次数 / 写入数 / 人工队列数）、`module2_computer_use`（动作数）、`module4_local_model`（识别次数 / 结果 / 问题码）、`module3_etax`（草稿 / 是否提交 / 提交单号）。**默认闸门无人值守，`--etax` 只产出草稿，一次提交请求都不会发出。**

## 2.5 真实取数抓取器（`scripts/live_capture.py`）

```bash
.venv/Scripts/python.exe scripts/live_capture.py --source lingxing --out captures/live
.venv/Scripts/python.exe scripts/sample50.py --source capture --capture-dir captures/live/<时间戳>
.venv/Scripts/python.exe scripts/live_capture.py --source dingtalk --out captures/live/dingtalk
```

抓取器逐次落盘请求（凭据打码）与原始响应体，并生成 `manifest.json`（endpoint / 时间 / HTTP 状态 / 响应 sha256 / 记录条数 / `origin: live`）。无凭据退出码 2 且不建目录；网络不通把失败请求写进 `failures[]`、`complete=false`、退出码 3。详见 `docs/LIVE_EVIDENCE.md`。

## 2.6 领星 v2 协议与钉钉区域接口的变量名对照

领星开放接口已切到官方 v2 协议，与早期版本的差别集中在三处，变量名与路径都做了兼容：

| 项 | v2 官方口径 | 变量 / 实现 |
| --- | --- | --- |
| 应用标识 | `appId`（不再叫 app_key） | `LINGXING_APP_ID`（旧名 `LINGXING_APP_KEY` 仍作别名，二者等价） |
| 签名 | 参数 ASCII 升序 → `k=v&` 拼接 → MD5 32 位大写 → AES/ECB/PKCS5（密钥=appId）→ Base64 → URL 编码 | `core/lingxing_crypto.py`；报文字段见 `tests/test_protocol_conformance.py` |
| 公共参数 | `app_key` / `timestamp` / `sign` 走 Query，`access_token` 走 `Authorization: Bearer`；业务参数放 JSON 体但**仍参与签名** | `core/lingxing_client.py` |
| 令牌 | `POST /api/auth-server/oauth/access-token`，`multipart/form-data`，字段 `appId`/`appSecret`；续约走 `/api/auth-server/oauth/refresh` | 同上；续约令牌 `LINGXING_REFRESH_TOKEN`（可选） |
| 货件 | `POST /erp/sc/data/fba_report/shipmentList`，必填 `sid` 与日期区间（左闭右开） | `LINGXING_SID` / `LINGXING_START_DATE` / `LINGXING_END_DATE` |
| 报关字段 | 中文报关名 / 材质 / 用途 / 报关单位 / 境内货源地 / HSCODE 来自报关资料明细 `customsDeclaration/batchDetail`，**不在产品列表** | `core/lingxing_map.py`（字段映射与缺口都写在这里） |
| 钉钉 | `/v1.0/doc/workbooks/{workbookId}/sheets/{sheetId}/ranges/{range}`，读 `values` 写 `values`，写入需 `operatorId` Query 参数与 `Document.Workbook.Write` 权限 | `core/dingtalk_client.py`；变量 `DINGTALK_WORKBOOK_ID` / `DINGTALK_SHEET_ID` / `DINGTALK_OPERATOR_ID` |

> 领星开放接口不是免费版能力，需要付费套餐；AppID/AppSecret 在开放后台「API 信息」页查看，同页要配 IP 白名单（只接受公网 IP）。开通条件与实测输出见 `docs/LIVE_EVIDENCE.md` 第 3、4 节。

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