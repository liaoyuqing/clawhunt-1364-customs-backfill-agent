# 跨境电商报关表回填自动化 Agent（四模块）

给一家做亚马逊 FBA 出口的跨境公司做的报关流水线：领星 ERP 与钉钉台账之间的数据搬运、组合产品拆分与单位换算、合同与发票逐项核对、发票原件归档与结余实时计算。**所有业务规则都在 `rules/*.yaml`，改规则不改代码。**

## 这套代码解决的四个模块

| 模块 | 目录 | 做什么 |
| --- | --- | --- |
| 一 下单确定 | `core/contract.py`、`core/naming.py` | 可报关产品判定与品名归并（去品牌词）、采购合同生成（合同品名=开票名=报关名强制一致）、要件检查与盖章回传件核验 |
| 二 报关表回填 | `core/declare.py`、`core/ledger.py`、`core/lingxing.py`、`core/dingtalk.py`、`core/vision.py` | 按批次号建行并逐字段回填，组合产品拆子 SKU、按套/按个换算、多批次合并与部分报关、缺失与勾稽提醒；接口覆盖不到的场景走视觉并标注来源 |
| 三 开票 | `core/invoice.py` | 品类匹配开票公司与联系人、生成开票通知、发票预览件与合同逐项核对输出差异清单（含抬头公司名与税号） |
| 四 发票回填与归档 | `core/archive.py`、`core/preflight.py` | 发票号/开票日期/耗用数量/剩余可用回填、结余 Decimal 实时计算、四类原件归档并回填共享链接；单独虚拟机 + 本地模型，启动三项自检 |

## 三条贯穿全场的设计

1. **每一格都能回溯**：`Ledger.write` 要求传 `SourceRef(kind, ref, at)`，`kind` 只能是 `api` / `vision` / `manual` / `config`；同一行同一列写第二次直接抛 `DuplicateWrite`，未知列抛 `UnknownColumn`。抽检脚本断言「50 批次 × 全部格子」`source.kind` 缺失数为 0。
2. **规则全部外部化**：品名去品牌词、组合拆分、单位换算、申报单价系数、必填与勾稽、开票公司与税号、台账列序，七张表各带 `version` 与 `effective_from`；`Rules(as_of=...)` 在生效日期未到时直接拒绝加载。
3. **对外提交必须过人工闸门**：`HumanGate.require_human(action, payload)` 在拿不到确认时抛 `HumanApprovalRequired`，测试断言「无确认时外部桩调用次数 == 0」，有确认时记一条审计行（操作人、动作、时间、载荷摘要前 8 位）。

## 安装与运行（Python 3.11+）

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest -q
```

预期输出（干净 clone 实测）：

```text
60 passed in 0.14s
```

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
scripts/          make_fixtures / reference_impl / sample50 / collect_metrics / secret_scan
tests/            9 个测试文件，60 个用例
fixtures/         合成源数据（batches/products/suppliers）与 expected.json
reports/          sample50_report.json / metrics.json / secret_scan_report.json
deploy/isolated/  模块四单独虚拟机的 compose 与启动自检
docs/             部署文档与操作手册
```

## 两套部署

**主环境**（模块一、二、三）：接领星与钉钉的开放平台应用，跑 `python -m core.cli --batch B2026-0001`。详见 `docs/deploy.md`。

**模块四独立虚拟机**：只接内网，出网白名单只留内网 CIDR，模型 base_url 指向本机；启动前 `python -m prepublish.check` 三项自检，任一不满足退出码 2。详见 `deploy/isolated/README.md`。

## 边界（本单不做）

退税链路（单一窗口导出、加密报关单导税局、出口发票匹配、免退税申报配单）不在本单范围，后续另发；电子税务局开票只做到「填单后停等人工提交」。