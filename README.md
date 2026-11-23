# 跨境电商报关表回填自动化 Agent（四模块）

给一家做亚马逊 FBA 出口的跨境公司做的报关流水线：领星 ERP 与钉钉台账之间的数据搬运、组合产品拆分与单位换算、合同与发票逐项核对、发票原件归档与结余实时计算。**所有业务规则都在 `rules/*.yaml`，改规则不改代码。**

## 这套代码解决的四个模块

| 模块 | 目录 | 做什么 |
| --- | --- | --- |
| 一 下单确定 | `core/contract.py`、`core/naming.py` | 可报关产品判定与品名归并（去品牌词）、采购合同生成（合同品名=开票名=报关名强制一致）、要件检查与盖章回传件核验 |
| 二 报关表回填 | `core/declare.py`、`core/ledger.py`、`core/lingxing.py`、`core/dingtalk.py`、`core/vision.py` | 按批次号建行并逐字段回填，组合产品拆子 SKU、按套/按个换算、多批次合并与部分报关、缺失与勾稽提醒；接口覆盖不到的场景走视觉并标注来源 |
| 三 开票 | `core/invoice.py`、`scripts/invoice_diff_report.py` | 品类匹配开票公司与联系人、生成开票通知、发票预览件与合同逐项核对输出差异清单（含抬头公司名与税号） |
| 四 发票回填与归档 | `core/module4.py`、`core/archive.py`、`core/preflight.py`、`prepublish/` | 发票号/开票日期/耗用数量/剩余可用回填、结余 Decimal 实时计算、四类原件归档并回填共享链接；单独虚拟机 + 本地模型，启动三项自检 |

## 三条贯穿全场的设计

1. **每一格都能回溯**：`Ledger.write` 要求传 `SourceRef(kind, ref, at)`，`kind` 只能是 `api` / `vision` / `manual` / `config`；同一行同一列写第二次直接抛 `DuplicateWrite`，未知列抛 `UnknownColumn`。抽检脚本断言「50 批次 × 全部 23 列」`source.kind` 缺失数为 0，一致率 100%（2401 可比格 = 2408 格 − 7 个源数据缺失的不可判定格）。
2. **规则全部外部化**：品名去品牌词、组合拆分、单位换算、申报单价系数、必填与勾稽、开票公司与税号、台账列序，七张表各带 `version` 与 `effective_from`；`Rules(as_of=...)` 在生效日期未到时直接拒绝加载。
3. **对外提交必须过人工闸门**：`HumanGate.require_human(action, payload)` 在拿不到确认时抛 `HumanApprovalRequired`，测试断言「无确认时外部桩调用次数 == 0」，有确认时记一条审计行（操作人、动作、时间、载荷摘要前 8 位），并同时追加到 `logs/audit.log`（JSONL，一行一条，可按 `batch_no` 检索）。

## 安装与运行（Python 3.11+）

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest -q
```

预期输出（干净 clone 实测）：

```text
124 passed
```

只依赖标准库 + `PyYAML`（规则包解析）+ `pytest`（自测），没有其它运行期依赖。

## 生成源数据与参考值

```bash
.venv/Scripts/python.exe scripts/make_fixtures.py    # 合成 50 批次 / 11 产品 / 3 供应商
.venv/Scripts/python.exe scripts/reference_impl.py   # 独立参考实现算出期望值
.venv/Scripts/python.exe scripts/sample50.py         # 50 批次逐格抽检，写 reports/sample50_report.json
```

`scripts/reference_impl.py` 刻意不复用 `core/` 任何代码，用 `fractions.Fraction` + 整数四舍五入重算一遍，两套代码同时错成一样的概率极低。

## 目录

```text
rules/            七张规则表（业务规则唯一来源）
core/             引擎：models/config/ledger/naming/price/units/bundle/checks/gates/
                  lingxing/dingtalk/vision/declare/contract/invoice/archive/preflight
core/module4.py   模块四回填引擎：按品类扣发票额度，写台账后 7 列（耗用/剩余/结余/归档链接）
core/knowledge.py 模块一品名知识库：报关名 -> 海关编码，同名复用不重复登记
core/pipeline.py  离线主流水线：模块二回填 -> 模块四发票/归档回填 -> 批次间勾稽
core/cli.py       命令行入口：python -m core.cli --batch <批次号>（模块一~四主流程，离线可跑）
prepublish/       模块四启动自检：python -m prepublish.check --config <yaml>（复用 core/preflight.py）
scripts/          make_fixtures / reference_impl / sample50 / collect_metrics / secret_scan /
                  invoice_diff_report（AC3 差异清单与期望自证）
tests/            19 个测试文件，124 个用例
fixtures/         合成源数据（batches/products/suppliers）、发票预览件（invoices/）与 expected.json
reports/          sample50_report.json / metrics.json / secret_scan_report.json /
                  invoice_diff_report.md / invoice_diff_report.json
deploy/isolated/  模块四单独虚拟机的 compose、自检配置样例与上线说明
docs/             部署文档与操作手册
```

## 两套部署

**主环境**（模块一、二、三）：接领星与钉钉的开放平台应用，跑

```bash
.venv/Scripts/python.exe -m core.cli --batch B2026-0001
```

`core/cli.py` 是一个**真实存在、可直接执行**的入口：默认回放仓库自带夹具（`fixtures/lingxing/`），不连领星、不连钉钉、不联网，对指定批次跑模块一（采购合同与要件检查）、模块二（批次回填并写钉钉台账的内存投影）、模块三（发票预览件与合同逐项核对）、模块四（按品类扣减发票额度并回填后 7 列），最后把结果打印成一份 JSON 摘要。真实接入时只把 `LingxingClient` 的 `transport` 换成开放平台实现，命令与参数不变。详见 `docs/deploy.md`。

**模块四独立虚拟机**：只接内网，出网白名单只留内网 CIDR，模型 base_url 指向本机；启动前跑

```bash
.venv/Scripts/python.exe -m prepublish.check --config deploy/isolated/isolated.example.yaml
```

三项自检（白名单只有私有网段 / 模型地址是回环或内网 / 原件目录不在云同步路径）全部复用 `core/preflight.py` 的判定函数，任一不过逐条打印 `problems` 并退出码 2，进程不启动。详见 `deploy/isolated/README.md`。

## 买家自验清单（不依赖交付方服务器）

在买家自己的机器上，从干净 clone 起逐条执行，全部离线、无外网调用：

1. 装依赖：`.venv/Scripts/python.exe -m pip install -r requirements.txt` —— 预期退出码 `0`（只有 PyYAML 与 pytest 两个包）。
2. 跑测试：`.venv/Scripts/python.exe -m pytest -q` —— 预期退出码 `0`，末行 `124 passed`。
3. 跑差异清单：`.venv/Scripts/python.exe scripts/invoice_diff_report.py` —— 预期退出码 `0`，打印 `invoices 6 clean 3 dirty 3 diffs 3 ... verified yes`，并生成 `reports/invoice_diff_report.md` 与 `reports/invoice_diff_report.json`。
4. 跑主流程：`.venv/Scripts/python.exe -m core.cli --batch B2026-0001` —— 预期退出码 `0`，stdout 是一份 JSON 摘要（`batch_no` / 行数 / 列数 / `module4_invoice_ledger` 的耗用与剩余 / 差异条数 / 闸门状态）；换个不存在的批次号则退出码 `1`、stderr 有中文原因。
5. 跑模块四自检：`.venv/Scripts/python.exe -m prepublish.check --config deploy/isolated/isolated.example.yaml` —— 预期退出码 `0`、末行 `ok`；把配置里的白名单换成公网 IP、模型地址换成公网域名、原件目录放进 OneDrive，再跑一次预期退出码 `2` 并逐条列出 3 条 `problems`。

以上 5 条都不需要访问领星、钉钉或任何外网地址。

## 一条命令跑完 50 批次（含模块四）

```bash
.venv/Scripts/python.exe scripts/sample50.py
```

分母是台账**全部 23 列**（模块二 16 列 + 模块四 7 列），不是只看模块二的 16 列：50 批次 146 行共 2408 格，其中 2401 格可判定（7 格因源数据 HSCODE 缺失记为不可判定），一致数 2401、差异 0、来源缺失 0，一致率 100%。报告写在 `reports/sample50_report.json`。

单位换算的原始单位与系数留痕在 `core.cli` 摘要的 `module2_fill.conversions` 与 `reports/unit_conversions.json`（50 批次共 23 条「套→个」换算），台账列序按 `rules/ledger_columns.yaml` 保持不变，因此不额外增设备注列 —— 换算前后都在留痕文件里可查。

## 未实现 / 主动降级清单

| 项 | 现状 | 为什么 |
| --- | --- | --- |
| 电子税务局开票 | 只做到「填单后停等人工提交」，无税局接口调用 | 客户要求有人工闸门，自动提交不在本单范围 |
| 退税链路 | 不做 | 工单明确列为后续另发（单一窗口导出、加密报关单、免退税配单） |
| 领星 / 钉钉真实开放平台连接 | 以 `FixtureTransport` 回放夹具代替，接口契约与分页逻辑一致 | 交付环境无客户凭据，联网与密钥均在本单硬约束之外 |
| 视觉补位真实模型 | `ScreenshotSource` 走注入的识别结果，不内置 OCR 模型 | 客户现场模型未知；接口、置信度阈值与转人工规则已实现 |
| 渠道列 | 台账 23 列里没有渠道列，合并判定时由调用方传 `channels` 映射 | 客户台账列序已锁定，新增列需客户确认 |

## 边界（本单不做）

退税链路（单一窗口导出、加密报关单导税局、出口发票匹配、免退税申报配单）不在本单范围，后续另发；电子税务局开票只做到「填单后停等人工提交」。
