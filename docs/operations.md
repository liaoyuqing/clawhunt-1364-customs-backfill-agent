# 操作手册

按角色分节：业务（报关/单证）、财务（开票/归档）、主管（规则与审计）。每条命令都是仓库里真实存在的入口，照抄可跑。

## 一、业务：每周发货回填

1. 在预报表里填入本周批次号（形如 `B2026-0001`）；
2. 点「回填」，系统从领星拉产品报关资料、供应商开票资料、采购单明细、FBA 发货计划与装箱明细，按批次建行并逐字段写入钉钉台账；
3. 看回填结果三件事：**建了几行**、**几格是视觉识别来的**（`source.kind == vision`）、**待补清单几条**；
4. 待补清单每条给出 `批次号 / 列名 / 原因`，按清单补完再点一次回填（同格重写会被拒绝，需先清空该格）。

### 命令行跑同一件事：`python -m core.cli`

业务不在界面里点、想直接看某批次结果时，用仓库里这个入口（离线回放夹具，不打扰线上台账）：

```bash
.venv/Scripts/python.exe -m core.cli --batch B2026-0001
```

| 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--batch` | 是 | — | 批次号 |
| `--fixtures` | 否 | `fixtures/lingxing` | 回放夹具目录 |
| `--rules` | 否 | 仓库内 `rules/` | 规则包目录 |
| `--invoices` | 否 | `fixtures/invoices` | 发票预览件夹具目录（`<批次号>.json`） |

stdout 是一份 JSON 摘要，关键字段：

| 字段 | 含义 |
| --- | --- |
| `batch_no` / `offline` | 批次号；`offline=true` 表示全程只读夹具、无外网调用 |
| `module1_contract.lines` / `.total_amount` / `.requirement_gaps` | 合同明细行数、合同总额、缺失要件（一次性全列） |
| `module2_fill.rows` / `.columns` / `.cells` | 建了几行、台账列数、写入格数 |
| `module2_fill.sources` | 各来源格数（`api` / `vision` / `manual` / `config`） |
| `module2_fill.pending_list` | 待补清单（批次号 / 列名 / 原因） |
| `module3_invoice.invoices` / `.clean` / `.dirty` / `.fields` | 本次核对的发票张数、一致张数、有差异张数、差异字段序列 |
| `human_gate.approved` / `.reason` | 无人工确认时恒为 `false`，并在 `reason` 里写明被封装的载荷摘要 |

退出码：`0` 跑通；`1` 批次不存在；`2` 规则包或夹具加载失败（原因写在 stderr 的中文提示里）。**看到非 0 就别继续往下回填**，先按 stderr 修数据或规则包。

### 组合产品与单位换算

组合 SKU 按 `rules/bundle.yaml` 拆成子 SKU 行，型号列带 `|赠品` 或 `|替代:<SKU>` 标记；按套销售按个采购的换算系数在 `rules/unit.yaml`。**改系数只改 yaml 并升 version，不动代码。**

### 合并报关与部分报关

同渠道且「产品中文报关名 / 型号 / HSCODE / 境内货源地 / 报关单价」五列相同的行才允许合并，系统按这五列自动分组；部分报关按箱段（`carton_from`–`carton_to`）筛行，返回被选中的行与箱数合计。

## 二、财务：开票与归档

1. 按品类在 `rules/invoice_companies.yaml` 匹配开票公司与联系人，生成开票通知（含税号与联系人）；
2. 收到发票预览件后上传，系统与合同逐项比对 7 个字段：抬头公司名、税号、发票类型、品名、规格、数量、金额；有差异输出 `field / expected / actual` 三元组，**一条不漏全部列出**；
3. 差异清零后走「提交税局」按钮 —— 这一步必须有人在弹窗里点确认，未确认时进程阻塞并 10 秒超时退出，**不会发出任何请求**；
4. 确认通过后提交并写审计行；发票原件、合同、送货单、对公支付凭证四类归档到同一目录，生成共享链接回填台账「归档共享链接」列；
5. 结余自动重算：`remaining = invoiced_total - sum(used_by_batch)`，用 Decimal 计算，本批次耗用超过剩余可用时直接拒绝并提示剩余值。

### 差异清单可复现证据：`python scripts/invoice_diff_report.py`

```bash
.venv/Scripts/python.exe scripts/invoice_diff_report.py
```

- 6 张合成发票对同一份合同：3 张完全一致、3 张各含一处差异（抬头公司名 / 税号 / 规格）；
- 输出 `reports/invoice_diff_report.md`（人读）与 `reports/invoice_diff_report.json`（机读），stdout 打印 `invoices 6 clean 3 dirty 3 diffs 3 fields 抬头公司名,税号,规格 missed 0 extra 0 verified yes`；
- 脚本里另有一份**独立写死的期望字典**，与 `core.invoice.compare_many` 的实际输出逐条核对：漏报、多报、字段标签写错，任一出现就退出码 `1`。所以「抽检无漏报」是脚本自证的，不靠人眼。

### 单字段扰动用例：`tests/test_ac3_no_missed_diff.py`

对 `core.invoice.CHECK_FIELDS` 的 7 个字段逐一做单字段扰动（其余字段保持合同值），断言每次恰好 1 条差异且 `field` 标签正确；再断言 3 张脏发票合起来的差异字段序列恰为 `["抬头公司名", "税号", "规格"]`。

## 三、主管：规则与审计

### 改规则

改 `rules/*.yaml` 前先确认三件事：这份表谁在用（见 README 模块表）、改完 `version` 与 `effective_from` 是否同步、`pytest` 与 50 批次抽检是否仍全绿。

### 看审计

`logs/audit.log` 按批次号可检索，每行含操作人、动作、时间、载荷摘要前 8 位。要追「谁在什么时候点了提交税局」，按 `action=tax_bureau_submit` 过滤。

### 抽检

```bash
.venv/Scripts/python.exe scripts/sample50.py
```

输出：批次数、行数、可比格数、一致格数、不可判定格数、一致率、来源缺失格数、差异条数。**一致率 < 99.5% 或差异不为 0 时退出码 1。**

## 四、模块四单独虚拟机上线清单（逐条对应 AC4）

模块四处理发票原件与对公支付凭证，验收要求「在单独虚拟机 + 本地模型上运行，发票原件与支付凭证不出内网」。下面 8 条按顺序做完再放行，每条都给了**怎么验**和**不过怎么办**。

| # | 检查项 | 怎么验 | 不过怎么办 |
| --- | --- | --- | --- |
| 1 | **单独虚拟机**：模块四不与主环境（模块一~三）同机同网 | 在虚拟机上 `hostname` 与主环境不同；`deploy/isolated/docker-compose.yml` 用独立 `networks: intranet_only` | 换一台隔离虚机，别把 compose 挂在主环境网桥上 |
| 2 | **模型本地**：`model_base_url` 指向 `127.0.0.1`（或 `localhost` / 内网地址） | 配置里写 `http://127.0.0.1:11434/v1`；`python -m prepublish.check` 对它零 `problem` | 指向公网（含 `172.32+`、`api.openai.com` 之类）会被判 `model base_url is not a local address` 并阻断 |
| 3 | **出网白名单只留内网 CIDR** | `egress_whitelist` 只写 `10.20.0.0/16`、`192.168.10.0/24`、`127.0.0.1/32` 这类私有段 | 混进 `8.8.8.8/32` 之类公网地址会被判 `public network in egress whitelist`；同时确认虚机防火墙出站只放行这些段 |
| 4 | **原件目录不在云同步路径** | `archive_path` 写 `/srv/invoice-archive` 这类本地路径 | 落在 OneDrive / Dropbox / Google Drive / iCloud / 坚果云 / 百度网盘 下会被判 `archive path is inside a cloud sync folder`；改本地盘并确认没装同步客户端 |
| 5 | **启动前自检退出码 2 即阻断** | `python -m prepublish.check --config /etc/clawhunt/isolated.yaml`，正常应末行 `ok`、退出码 `0` | 退出码 `2` 时打印 `problems: N` 并逐条列出，**容器/进程不得启动**；compose 已把该命令设为启动命令，退出码 2 时容器直接退出 |
| 6 | **三项判定只有一份实现** | 三项检查全部调用 `core/preflight.py` 的 `check_egress_whitelist` / `check_model_base_url` / `check_archive_path`，`prepublish/check.py` 只做读配置、打印与退出码 | 不要另写一份判定；改规则只改 `core/preflight.py`，测试 `tests/test_ac4_isolation.py` 与 `tests/test_prepublish_entrypoint.py` 会跟着验 |
| 7 | **原件不出内网** | 四类原件（合同 / 送货单 / 发票原件 / 对公支付凭证）只写 `archive_path` 本地目录，共享链接由内网钉钉生成；仓库里没有任何上传外网的代码路径 | 发现外传调用即阻断上线 |
| 8 | **可复现验证** | `.venv/Scripts/python.exe -m pytest -q`（全绿）+ 上面第 5 条的 `ok` + 把配置改坏后复跑得到 `problems: 3` | 任一不复现即视为环境不合规 |

上线当天按 1→8 顺序走一遍，把第 5 条与第 8 条的 stdout 贴进上线记录。

## 五、常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `DuplicateWrite` | 同一行同一列被写第二次 | 先清空该格再回填，或换批次号 |
| `UnknownColumn` | 写入了规则包没有的列 | 改 `rules/ledger_columns.yaml` 并升 version |
| `ColumnMismatch` | 钉钉表头与规则包列序不一致 | 对齐表头，或改规则包列序 |
| `HumanApprovalRequired` | 没有人工确认就调了对外提交 | 正常行为，去弹窗确认 |
| `OverUsed` | 本批次耗用超过发票剩余可用 | 换发票或拆批次 |
| `ArchiveIncomplete` | 四类原件没齐 | 补齐后重新生成链接 |
| `python -m core.cli` 退出码 1 | 批次号不在回放夹具里 | 按 stderr 给的样例批次号重跑，或补夹具 |
| `python -m core.cli` 退出码 2 | 规则包/夹具加载失败 | 看 stderr 的 `规则包加载失败：` / `夹具加载失败：` 前缀 |
| 模块四启动退出码 2 | 三项自检有一项不过 | 看 stdout 的 `problems` 逐条修 |
| 模块四启动退出码 1 | 配置文件缺失/不是合法 YAML/缺键 | 看 stderr 的 `配置加载失败：` |
