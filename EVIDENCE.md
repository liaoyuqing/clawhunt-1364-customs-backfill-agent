# 验收证据总表（AC1–AC6 与附加断言）

本文件是给评审的**唯一入口**：每条验收标准对应一条可复制的复现命令、一个可直接观察的结果、以及断言落在哪一行代码。所有路径与行号均按 `git ls-tree -r HEAD --name-only` 与源码核对过，实际存在。

约定：命令都在仓库根目录执行，解释器为 `.venv/Scripts/python.exe`；全部命令离线可跑，不需要外网，也不需要客户账号（真实账号只在第 7 节单列）。

---

## 1. 验收标准逐条对照

| 验收编号 | 复现命令 | 预期可观察结果 | 证据文件路径 | 断言所在代码位置（文件:行） |
| --- | --- | --- | --- | --- |
| **AC1** 模块二回填 + 50 批次逐字段一致率 ≥99.5%（源数据自身错误不计入）+ 结余正确 + 缺失项进待补 | `.venv/Scripts/python.exe scripts/sample50.py --source recorded` | 末行 `batches 50 rows 146 columns 23 compared 2401 consistent 2401 undecidable 7 rate 1.000000 source_missing 0 diffs 0`，退出码 0；报告里 `source_error_count` 为 4 且不进分母 | `reports/sample50_report.json`、`fixtures/expected.json`、`AC1_CONSISTENCY_REPORT.md` | `tests/test_ac1_fill_consistency.py:15`（一致率）、`:25`（每格来源）、`:40`（待补清单）、`:52`（HSCODE 反例）；结余 `tests/test_module4_fill.py:49`、`:63`、`:96` |
| **AC2** 组合产品与按套/按个换算按规则拆分并带标记 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_ac2_bundle_and_units.py tests/test_ac2_unit_pipeline.py` | 用例全绿；拆分明细与 `rules/bundle.yaml` 一致，型号列带 `\|赠品` / `\|替代:<SKU>`；套→个×12 后写「个 / 96 / 0.53625」 | `rules/bundle.yaml`、`rules/unit.yaml`、`reports/unit_conversions.json` | `tests/test_ac2_bundle_and_units.py:31`（拆分）、`:36`（赠品与替代料标记）；`tests/test_ac2_unit_pipeline.py:14`（换算写入）、`:51`（单位缺失报错） |
| **AC3** 模块三发票与合同七字段逐项核对输出差异清单（含抬头公司名与税号），抽检无漏报 | `.venv/Scripts/python.exe scripts/invoice_diff_report.py` | stdout `invoices 6 clean 3 dirty 3 diffs 3 fields 抬头公司名,税号,规格 missed 0 extra 0 verified yes`，退出码 0 | `reports/invoice_diff_report.md`、`reports/invoice_diff_report.json` | `tests/test_ac3_invoice_diff.py:44`（七字段逐一）、`:51`（3 净 3 脏）；`tests/test_ac3_no_missed_diff.py:40`（单字段扰动恰好 1 条）、`:55`（差异序列恰为三条） |
| **AC4** 模块四在单独虚拟机 + 本地模型运行，原件不出内网 | `.venv/Scripts/python.exe -m prepublish.check --config deploy/isolated/isolated.example.yaml` | 末行 `ok`，退出码 0；把白名单/模型地址/原件目录改坏后退出码 2 并逐条列出 `problems: 3` | `deploy/isolated/isolated.example.yaml`、`deploy/isolated/README.md`、`core/preflight.py` | `tests/test_ac4_isolation.py:31`（公网 CIDR 拒绝）、`:45`（模型地址必须本地）、`:55`（原件目录不得云同步）、`:62`（三条问题一次全列）；`tests/test_module4_local_model.py:65`（公网地址零请求） |
| **AC5** 涉及税局的操作保留人工确认，且有操作日志 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_ac5_human_gate.py tests/test_audit_log_persist.py tests/test_cli_real_paths.py` | 用例全绿；无确认时外部桩调用 0 次，确认后恰 1 次并写审计行 | `logs/audit.log`（运行后生成）、`scripts/audit_query.py` | `tests/test_ac5_human_gate.py:25`（无确认零提交）、`:42`（确认后一次 + 审计行）；`tests/test_audit_log_persist.py:18`（JSONL 落盘五字段）；`tests/test_cli_real_paths.py:123`（电子税务局未确认零请求）、`:133`（确认后一次） |
| **AC6** 各模块源码 + 部署文档 + 操作手册，可独立运行，不依赖交付方服务器 | `.venv/Scripts/python.exe -m pytest -o addopts= -q` | 末行 `172 passed`，退出码 0；`python -m core.cli --batch B2026-0001` 退出码 0 且输出 JSON 摘要 | `README.md`、`docs/deploy.md`、`docs/operations.md`、`docs/modules_and_pricing.md` | `tests/test_packaging.py:38`（镜像用 slim 基础镜像）、`:50`（compose 有 build 与 healthcheck）；`tests/test_module1_knowledge_persist.py:65`（跨进程持久化） |

---

## 2. 附加断言（单位换算、授权与退款边界等）

| 断言 | 复现命令 | 预期可观察结果 | 证据文件路径 | 断言位置（文件:行） |
| --- | --- | --- | --- | --- |
| 单位换算读配置不硬编码 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_ac2_bundle_and_units.py::test_ac2_units_come_from_yaml` | 把 `套->个` 从 12 改成 10，换算结果变成 30，原配置仍是 36 | `rules/unit.yaml` | `tests/test_ac2_bundle_and_units.py:59` |
| 换算前后留痕 | `.venv/Scripts/python.exe scripts/sample50.py` | 生成 `reports/unit_conversions.json`，含原始单位/原始数量/系数/折算后数量 | `reports/unit_conversions.json` | `tests/test_ac2_unit_pipeline.py:30` |
| 勾稽三条都能真报 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_reconciliation_rules.py` | R1/R2/R3 三条都在反例夹具上报出，干净 50 批次无误报 | `fixtures/reconciliation/` | `tests/test_reconciliation_rules.py:17`、`:26`、`:35`、`:44` |
| 数据来源必须标注，合成口径不能冒充真实源 | `.venv/Scripts/python.exe scripts/sample50.py --source fixture` | 报告 `source` 为 `fixture`、`source_label` 以 `source=fixture(synthetic)` 开头 | `reports/sample50_report.json` | `tests/test_ac7_sample_source.py:39`、`:67`、`:91` |
| 真实接口缺凭据必须报错，不静默回退 | `.venv/Scripts/python.exe scripts/sample50.py --source lingxing` | 退出码 2，stderr 含 `LINGXING_BASE_URL` | `scripts/source_data.py` | `tests/test_ac7_sample_source.py:91` |
| 视觉低置信度不写台账 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_vision_real_path.py` | 高置信度字段写入且来源为 `vision`，低置信度只进人工队列 | `fixtures/recorded/vision_response.json` | `tests/test_vision_real_path.py:64` |
| 模块四本地模型地址必须过内网白名单 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_module4_local_model.py` | 公网地址被拦且请求数为 0；私有段与回环放行 | `core/preflight.py` | `tests/test_module4_local_model.py:65` |
| 品名两步确认未完成不得写确认编码 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_module1_knowledge_persist.py` | 未确认时报 `name_not_confirmed` 且 HSCODE 留空；确认后写确认编码且来源为 `manual` | `core/knowledge.py` | `tests/test_module1_knowledge_persist.py:38`、`:146` |
| 电子税务局无凭据只出草稿 | `.venv/Scripts/python.exe -m core.cli --batch B2026-0001` | 摘要 `module3_etax.submitted` 为 `false`，`pending` 里列出待提交草稿 | `core/etax.py` | `tests/test_cli_real_paths.py:123` |

---

## 3. 一键全量自证

```bash
cd <仓库根目录>

# 1) 全部单元测试（172 条）
.venv/Scripts/python.exe -m pytest -q

# 2) 50 批次逐字段抽检（一致率 / 结余 / 待补清单）
.venv/Scripts/python.exe scripts/sample50.py --source recorded

# 3) 独立参考实现重算期望值
.venv/Scripts/python.exe scripts/reference_impl.py --source recorded

# 4) 发票差异清单（AC3）
.venv/Scripts/python.exe scripts/invoice_diff_report.py

# 5) 模块四启动自检（AC4）
.venv/Scripts/python.exe -m prepublish.check --config deploy/isolated/isolated.example.yaml

# 6) 主流程入口（AC6）
.venv/Scripts/python.exe -m core.cli --batch B2026-0001

# 7) 密钥扫描
.venv/Scripts/python.exe scripts/secret_scan.py
```

七条命令的预期退出码依次为 `0 0 0 0 0 0 0`。

---

## 4. 各模块对应的源码与文档

| 模块 | 源码 | 文档 |
| --- | --- | --- |
| 一 下单确定 | `core/contract.py`、`core/naming.py`、`core/knowledge.py` | `README.md` 模块表、`docs/operations.md` |
| 二 报关表回填 | `core/declare.py`、`core/ledger.py`、`core/bundle.py`、`core/units.py`、`core/checks.py`、`core/lingxing_client.py`、`core/dingtalk_client.py`、`core/vision_client.py`、`core/computer_use.py` | `docs/deploy.md` 第 2.4 节（接入步骤） |
| 三 开票 | `core/invoice.py`、`core/etax.py`、`scripts/invoice_diff_report.py` | `docs/modules_and_pricing.md` |
| 四 发票回填与归档 | `core/module4.py`、`core/archive.py`、`core/preflight.py`、`core/local_model.py`、`prepublish/` | `deploy/isolated/README.md`、`docs/operations.md` 第四节 |

---

## 5. 证据文件清单

| 文件 | 内容 |
| --- | --- |
| `AC1_CONSISTENCY_REPORT.md` | AC1 的完整口径、逐条源错误、复现命令、结余与待补清单 |
| `EVIDENCE.md` | 本文件：六条验收 + 附加断言的命令 / 结果 / 路径 / 断言行号 |
| `reports/sample50_report.json` | 50 批次抽检机读结果（一致率、差异、来源、源错误） |
| `fixtures/expected.json` | 独立参考实现算出的期望值，覆盖 23 列 |
| `reports/invoice_diff_report.md` / `.json` | AC3 六张发票的差异清单 |
| `reports/unit_conversions.json` | 单位换算留痕 |
| `reports/metrics.json` | 全部实测数字（用例数、文件数、行数、树哈希） |
| `reports/secret_scan_report.json` | 密钥扫描结果 |

---

## 6. 运行环境

- Python 3.11+，运行期只依赖 `PyYAML`，自测只依赖 `pytest`（见 `requirements.txt`）。
- 全部命令只读仓库内文件，不访问任何外网地址，不需要交付方服务器。
- Windows 控制台若为非 UTF-8 代码页（如 cp936），入口脚本与测试均已做编码处理，行为不受影响。

---

## 7. 需要客户账号才能复验的部分

下列命令在**拿到客户账号后**才有意义；代码路径已就位，未配凭据时会明确报错退出码 2，不会静默回退：

```bash
# 真实领星 / 钉钉接口取数与写回
.venv/Scripts/python.exe scripts/recorded_smoke.py --source live
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 --source lingxing
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 --source dingtalk --write-sheet

# 真实视觉模型与界面操作
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 \
  --vision-image "http://127.0.0.1:9000/shot-{row_key}.png" --computer-use

# 模块四本地模型识别发票原件
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 \
  --local-model-image "http://127.0.0.1:8000/invoice-{row_key}.png" \
  --egress-whitelist "10.20.0.0/16,192.168.10.0/24"

# 电子税务局草稿（提交仍需人工确认）
.venv/Scripts/python.exe -m core.cli --batch B2026-0001 --etax
```

环境变量清单见 `docs/deploy.md` 第 2.4 节；仓库内不含任何地址与凭据。
