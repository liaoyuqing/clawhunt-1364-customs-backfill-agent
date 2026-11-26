# 验收证据总表（AC1–AC6 与附加断言）

本文件是给评审的**唯一入口**：每条验收标准对应一条可复制的复现命令、一个可直接观察的结果、以及断言落在哪一行代码。所有路径与行号均按 `git ls-tree -r HEAD --name-only` 与源码核对过，实际存在。

约定：命令都在仓库根目录执行，解释器为 `.venv/Scripts/python.exe`；第 1–6 节的命令全部离线可跑，不需要外网也不需要客户账号。**依赖真实账号与真实网络的部分单列在 `docs/LIVE_EVIDENCE.md`**，那里同时给了客户侧一条命令复现的路径，以及我方网络打不通时的原始输出。

关于 AC1 的口径先说明白（买方第 2 次驳回的正题）：仓库离线跑出的一致率来自**合成源**，它证明的是「回填引擎与独立参考实现自洽」，**不是**「与客户源系统字段级一致率达标」。报告里 `counts_as_ac1_evidence` 因此为 `false`、`ac1_evidence_status` 为 `unverified_on_live_account`，只有 `source_origin=live` 时才会翻成 `true`。

---

## 1. 验收标准逐条对照

| 验收编号 | 复现命令 | 预期可观察结果 | 证据文件路径 | 断言所在代码位置（文件:行） |
| --- | --- | --- | --- | --- |
| **AC1** 模块二回填 + 50 批次逐字段一致率 ≥99.5%（源数据自身错误不计入）+ 结余正确 + 缺失项进待补 | 离线（引擎自洽性）：`.venv/Scripts/python.exe scripts/sample50.py --source recorded`；真实账号（AC1 达标证据）：`scripts/live_capture.py --source lingxing` → `scripts/sample50.py --source capture --capture-dir captures/live/<时间戳>` | 离线：末行 `batches 50 rows 146 columns 23 compared 2401 consistent 2401 undecidable 7 rate 1.000000 source_missing 0 diffs 0`，退出码 0，报告里 `source_origin=recorded`、`counts_as_ac1_evidence=false`、`ac1_evidence_status=unverified_on_live_account`；真实源：`source_origin=live`、`ac1_evidence_status=verified_on_live_account`（本轮未取得，原因见 `docs/LIVE_EVIDENCE.md` 第 4 节） | `reports/sample50_report.json`、`fixtures/expected.json`、`AC1_CONSISTENCY_REPORT.md`、`docs/LIVE_EVIDENCE.md` | `tests/test_ac1_fill_consistency.py:15`（一致率）、`:25`（每格来源）、`:40`（待补清单）、`:52`（HSCODE 反例）；结余 `tests/test_module4_fill.py:49`、`:63`、`:96`；证据口径 `tests/test_ac7_sample_source.py`（recorded 不计证据 / capture 拒绝非 live） |
| **AC2** 组合产品与按套/按个换算按规则拆分并带标记 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_ac2_bundle_and_units.py tests/test_ac2_unit_pipeline.py` | 用例全绿；拆分明细与 `rules/bundle.yaml` 一致，型号列带 `\|赠品` / `\|替代:<SKU>`；套→个×12 后写「个 / 96 / 0.53625」 | `rules/bundle.yaml`、`rules/unit.yaml`、`reports/unit_conversions.json` | `tests/test_ac2_bundle_and_units.py:31`（拆分）、`:36`（赠品与替代料标记）；`tests/test_ac2_unit_pipeline.py:14`（换算写入）、`:51`（单位缺失报错） |
| **AC3** 模块三发票与合同七字段逐项核对输出差异清单（含抬头公司名与税号），抽检无漏报 | `.venv/Scripts/python.exe scripts/invoice_diff_report.py` | stdout `invoices 6 clean 3 dirty 3 diffs 3 fields 抬头公司名,税号,规格 missed 0 extra 0 verified yes`，退出码 0 | `reports/invoice_diff_report.md`、`reports/invoice_diff_report.json` | `tests/test_ac3_invoice_diff.py:44`（七字段逐一）、`:51`（3 净 3 脏）；`tests/test_ac3_no_missed_diff.py:40`（单字段扰动恰好 1 条）、`:55`（差异序列恰为三条） |
| **AC4** 模块四在单独虚拟机 + 本地模型运行，原件不出内网 | `.venv/Scripts/python.exe -m prepublish.check --config deploy/isolated/isolated.example.yaml` | 末行 `ok`，退出码 0；把白名单/模型地址/原件目录改坏后退出码 2 并逐条列出 `problems: 3` | `deploy/isolated/isolated.example.yaml`、`deploy/isolated/README.md`、`core/preflight.py` | `tests/test_ac4_isolation.py:31`（公网 CIDR 拒绝）、`:45`（模型地址必须本地）、`:55`（原件目录不得云同步）、`:62`（三条问题一次全列）；`tests/test_module4_local_model.py:65`（公网地址零请求） |
| **AC5** 涉及税局的操作保留人工确认，且有操作日志 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_ac5_human_gate.py tests/test_audit_log_persist.py tests/test_cli_real_paths.py` | 用例全绿；无确认时外部桩调用 0 次，确认后恰 1 次并写审计行 | `logs/audit.log`（运行后生成）、`scripts/audit_query.py` | `tests/test_ac5_human_gate.py:25`（无确认零提交）、`:42`（确认后一次 + 审计行）；`tests/test_audit_log_persist.py:18`（JSONL 落盘五字段）；`tests/test_cli_real_paths.py:123`（电子税务局未确认零请求）、`:133`（确认后一次） |
| **AC6** 各模块源码 + 部署文档 + 操作手册，可独立运行，不依赖交付方服务器 | `.venv/Scripts/python.exe -m pytest -o addopts= -q` | 末行 `217 passed`，退出码 0；`python -m core.cli --batch B2026-0001` 退出码 0 且输出 JSON 摘要 | `README.md`、`docs/deploy.md`、`docs/operations.md`、`docs/modules_and_pricing.md` | `tests/test_packaging.py:38`（镜像用 slim 基础镜像）、`:50`（compose 有 build 与 healthcheck）；`tests/test_module1_knowledge_persist.py:65`（跨进程持久化） |

---

## 2. 附加断言（单位换算、授权与退款边界等）

| 断言 | 复现命令 | 预期可观察结果 | 证据文件路径 | 断言位置（文件:行） |
| --- | --- | --- | --- | --- |
| 单位换算读配置不硬编码 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_ac2_bundle_and_units.py::test_ac2_units_come_from_yaml` | 把 `套->个` 从 12 改成 10，换算结果变成 30，原配置仍是 36 | `rules/unit.yaml` | `tests/test_ac2_bundle_and_units.py:59` |
| 换算前后留痕 | `.venv/Scripts/python.exe scripts/sample50.py` | 生成 `reports/unit_conversions.json`，含原始单位/原始数量/系数/折算后数量 | `reports/unit_conversions.json` | `tests/test_ac2_unit_pipeline.py:30` |
| 勾稽三条都能真报 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_reconciliation_rules.py` | R1/R2/R3 三条都在反例夹具上报出，干净 50 批次无误报 | `fixtures/reconciliation/` | `tests/test_reconciliation_rules.py:17`、`:26`、`:35`、`:44` |
| 数据来源必须标注，合成口径不能冒充真实源 | `.venv/Scripts/python.exe scripts/sample50.py --source fixture` | 报告 `source` 为 `fixture`、`source_origin` 为 `fixture`、`counts_as_ac1_evidence=false` | `reports/sample50_report.json` | `tests/test_ac7_sample_source.py`（origin 口径、recorded 不计证据、capture 拒绝非 live / 非领星 / 不完整目录） |
| 真实接口缺凭据必须报错，不静默回退 | `.venv/Scripts/python.exe scripts/sample50.py --source lingxing` | 退出码 2，stderr 含 `LINGXING_BASE_URL`（缺 appId/appSecret 时含 `LINGXING_APP_ID`） | `scripts/source_data.py` | `tests/test_ac7_sample_source.py`（`test_lingxing_source_requires_environment_variables`） |
| 抓取器本身的无凭据 / 断网行为 | `.venv/Scripts/python.exe scripts/live_capture.py --source lingxing --out captures/live` | 无凭据：退出码 2、stderr 含 `LINGXING_APP_ID`、**不创建** `captures/live`；断网：失败请求进 `failures[]`、`manifest.complete=false`、退出码 3 | `scripts/live_capture.py`、`docs/LIVE_EVIDENCE.md` | `tests/test_protocol_conformance.py`（报文形态 19 条） |
| 建表入口探测：平台拒绝不算抓取失败，也不冒充成功 | `.venv/Scripts/python.exe scripts/live_capture.py --source dingtalk --out captures/dingtalk --probe-create` | 8 个创建入口逐条发送；403/400/404 进 `denials[]`，`complete=true`、`failure_count=0`、`denial_count=8`；**非探测模式**下同样的 4xx 进 `failures[]` 且 `complete=false` | `captures/dingtalk/20261126T174944Z/manifest.json`、`docs/LIVE_EVIDENCE.md` 5.3 节 | `tests/test_capture_redaction.py`（`test_denials_do_not_make_a_probe_capture_incomplete`、`test_endpoint_name_separates_workbook_create_from_sheet_listing`、`test_probe_capture_never_claims_customer_evidence`） |
| 领星签名协议与官方文档逐步对齐 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_protocol_conformance.py` | 19 条全绿：FIPS-197 AES 向量、MD5 大写、ASCII 升序、空值跳过 / null 参与、AES-ECB-PKCS5 + Base64、Query 仅 app_key/timestamp/sign、签名覆盖 body、sign 不缓存、3001008 退避 | `core/lingxing_crypto.py`、`core/lingxing_client.py` | `tests/test_protocol_conformance.py` |
| 视觉低置信度不写台账 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_vision_real_path.py` | 高置信度字段写入且来源为 `vision`，低置信度只进人工队列 | `fixtures/recorded/vision_response.json` | `tests/test_vision_real_path.py:64` |
| 模块四本地模型地址必须过内网白名单 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_module4_local_model.py` | 公网地址被拦且请求数为 0；私有段与回环放行 | `core/preflight.py` | `tests/test_module4_local_model.py:65` |
| 品名两步确认未完成不得写确认编码 | `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_module1_knowledge_persist.py` | 未确认时报 `name_not_confirmed` 且 HSCODE 留空；确认后写确认编码且来源为 `manual` | `core/knowledge.py` | `tests/test_module1_knowledge_persist.py:38`、`:146` |
| 电子税务局无凭据只出草稿 | `.venv/Scripts/python.exe -m core.cli --batch B2026-0001` | 摘要 `module3_etax.submitted` 为 `false`，`pending` 里列出待提交草稿 | `core/etax.py` | `tests/test_cli_real_paths.py:123` |

---

## 3. 一键全量自证

```bash
cd <仓库根目录>

# 1) 全部单元测试（217 条）
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

# 8) 协议一致性（不联网，只校验报文形态与算法向量）
.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_protocol_conformance.py

# 9) 真实取数抓取器：无凭据时的行为自检（应当退出码 2，且不创建 captures/live）
.venv/Scripts/python.exe scripts/live_capture.py --source lingxing --out captures/live
```

前八条命令的预期退出码依次为 `0 0 0 0 0 0 0 0`；第 9 条**故意**预期退出码 2（缺凭据就要明确报错，不静默回退）。

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
| `docs/LIVE_EVIDENCE.md` | 真实取数/写台账证据：客户侧三步复现、manifest 字段、领星开通条件、我方 TCP 不可达实测、钉钉 403 原文与写入样例、live/fixture 诚实对照 |
| `captures/dingtalk/<时间戳>/` | 钉钉侧真实抓取目录（令牌请求/响应留证 + 权限前后的失败/拒绝原文 + manifest），凭据已打码；`20261126T172559Z` 是读写权限开通后重抓（403→400 `MissingoperatorId`），`20261126T180011Z` 是知识库权限补齐后重跑建表入口（wiki 三条由 403 变 `400 MissingoperatorId`，建文档那条稳定 500 `internalError`，如实记 `complete=false`） |

---

## 6. 运行环境

- Python 3.11+，运行期只依赖 `PyYAML`，自测只依赖 `pytest`（见 `requirements.txt`）。
- 全部命令只读仓库内文件，不访问任何外网地址，不需要交付方服务器。
- Windows 控制台若为非 UTF-8 代码页（如 cp936），入口脚本与测试均已做编码处理，行为不受影响。

---

## 7. 需要客户账号才能复验的部分

下列命令在**拿到客户账号后**才有意义；代码路径已就位，未配凭据时会明确报错退出码 2，不会静默回退。
完整的取数→落盘→重算流程、我方网络实测输出、钉钉 403 原文与取得权限后的写入样例都在 `docs/LIVE_EVIDENCE.md`。

```bash
# 真实取数并落盘（AC1 的达标证据要从这里开始）
.venv/Scripts/python.exe scripts/live_capture.py --source lingxing --out captures/live
.venv/Scripts/python.exe scripts/sample50.py --source capture --capture-dir captures/live/<时间戳>

# 钉钉侧探令牌 / 读表 / 读区域（三个 id 齐了以后加 --write-sample 直接验证写入）
.venv/Scripts/python.exe scripts/live_capture.py --source dingtalk --out captures/live/dingtalk --operator-id <操作人 unionId> --write-sample PROBE

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

环境变量清单见 `docs/deploy.md` 第 2.4 节（领星 v2 用 `LINGXING_APP_ID`，旧的 `LINGXING_APP_KEY` 仍作别名兼容）；仓库内不含任何地址与凭据。