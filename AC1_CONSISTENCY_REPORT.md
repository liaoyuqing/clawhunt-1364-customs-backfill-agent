# AC1 一致率与结余、待补清单证据

本文件是验收第 1 条的**根目录证据入口**，供评审直接定位。全部数字由脚本现算，复现命令见下；文中每一处数字都可以用第 2 节的命令重跑得到同一结果。

> **必须先说清的证据口径（买方第 2 次驳回的正题）**
>
> 本文件第 1 节的 `1.000000` 来自**合成源**（`fixtures/recorded/*.SYNTHETIC.json`，每个文件自带 `_provenance: "local synthetic replay, not a capture from the vendor API"`）。它证明的是：**回填引擎与独立参考实现在 2408 个格子上逐格一致**，即引擎自身没有实现偏差。
>
> 它**不是**「与客户源系统字段级一致率 ≥99.5%」的达标证据。因此报告里 `counts_as_ac1_evidence` 为 `false`、`ac1_evidence_status` 为 `unverified_on_live_account`，并附了原因与客户侧三步复现步骤。
>
> 真实账号上的一致率本轮**未取得**，原因有二（均为可复核的客观条件，不是工作量问题）：
> 1. 领星开放接口（OpenAPI）不是免费版能力，需要付费套餐才开通，也没有公开公共沙箱；
> 2. `openapi.lingxing.com:443` 从交付方网络 TCP 不可达（DNS 解析到 `114.132.29.163`，`TcpTestSucceeded: False`，`curl` 退出码 28 超时），而 `apidoc.lingxing.com` 与 `api.dingtalk.com` 均可达。
>
> 客户侧把它补上的路径只有一条命令：`scripts/live_capture.py --source lingxing` → `scripts/sample50.py --source capture --capture-dir captures/live/<时间戳>`，报告里 `source_origin` 就会变成 `live`、`ac1_evidence_status` 变成 `verified_on_live_account`。完整步骤、manifest 字段、我方实测输出见 **`docs/LIVE_EVIDENCE.md`**。

---

## 1. 口径

| 项 | 实测值 |
| --- | --- |
| 抽检批次数 | 50 |
| 台账行数 | 146（50 个批次表头行 + 96 条明细行） |
| 台账列数 | 23 |
| 逐格比对总格数 | 2408 |
| 其中表头行格数 | 50 × 4 = 200（表头行写批次号/发货单号/合同号/归档共享链接 4 列，不含明细列） |
| 其中明细行格数 | 96 × 23 = 2208 |
| 可判定格数（分母） | 2401 |
| 一致格数 | 2401 |
| 不可判定格数（不计入分母） | 7 |
| 差异条数 | 0 |
| 一致率 | 1.000000（阈值 0.995） |
| 来源元数据缺失格数 | 0 |
| 源数据自身错误条数 | 4 |
| 数据来源 | `recorded`（本地合成回放） |
| `source_origin` | `recorded`（非 live） |
| `counts_as_ac1_evidence` | `false` |
| `ac1_evidence_status` | `unverified_on_live_account` |

上表 2401/2401 的一致率是在**合成源**上得到的；下面两行的 `false` / `unverified_on_live_account` 是脚本对这份数字的自我限定，防止它被当成 AC1 的验收结论。

分母口径写死为 `一致格数 + 差异条数`；`不可判定格` 单独成列、不进分母，正是客户要求的「源数据自身错误不计入」。

### 1.1 7 个不可判定格逐条列出

| 行键 | 列 | 原因 | 说明 |
| --- | --- | --- | --- |
| `B2026-0011#0` | HSCODE | `missing_hscode` | 源产品记录 HSCODE 为空 |
| `B2026-0013#0` | 型号 | `missing_model` | 源产品记录型号为空 |
| `B2026-0022#0` | HSCODE | `missing_hscode` | 源产品记录 HSCODE 为空 |
| `B2026-0026#0` | 型号 | `missing_model` | 源产品记录型号为空 |
| `B2026-0033#0` | HSCODE | `missing_hscode` | 源产品记录 HSCODE 为空 |
| `B2026-0039#0` | 型号 | `missing_model` | 源产品记录型号为空 |
| `B2026-0044#0` | HSCODE | `missing_hscode` | 源产品记录 HSCODE 为空 |

### 1.2 4 条 `source_errors` 逐条列出（不计入分母）

| 行键 | 列 | 原因 |
| --- | --- | --- |
| `B2026-0011#0` | HSCODE | `missing_hscode` |
| `B2026-0022#0` | HSCODE | `missing_hscode` |
| `B2026-0033#0` | HSCODE | `missing_hscode` |
| `B2026-0044#0` | HSCODE | `missing_hscode` |

这 4 条都来自同一批源数据缺陷：SKU-1006 在领星侧的 HSCODE 字段为空（`fixtures/lingxing/products.json` 里 `hscode: ""`）。它们写进 `fixtures/expected.json` 的 `source_errors`，`scripts/sample50.py` 读这个列表时**只做统计与展示，不加进分母**。

---

## 2. 复现

```bash
cd <仓库根目录>
.venv/Scripts/python.exe scripts/sample50.py --source recorded
```

真实 stdout：

```text
batches 50 rows 146 columns 23 compared 2401 consistent 2401 undecidable 7 rate 1.000000 source_missing 0 diffs 0
source recorded origin recorded host local files (no network) fetched_at 2026-11-27T00:45:16+08:00
source_errors 4 (源系统自身错误，不计入分母)
ac1_evidence unverified_on_live_account counts_as_ac1_evidence=false
说明：未在真实账号上取得（原因：领星开放接口需付费套餐才开通，且 openapi.lingxing.com 从交付方网络 TCP 443 不可达）；本文件的一致率来自合成源，只证明回填引擎与独立参考实现自洽
report -> reports/sample50_report.json
```

**退出码：0。** 判定规则写在 `scripts/sample50.py` 的 `main()` 末行：`rate >= 0.995` 且 `source_missing == 0` 且 `diffs` 为空才返回 0，否则返回 1。注意这条判定只谈**引擎自洽性**，第二行之后的 `ac1_evidence ...` 与 `说明：` 两行是脚本对「这份数字能不能当 AC1 证据」的自我限定。

### 2.1 真实账号上怎么补上这一格

四条路汇到同一段比对逻辑，区别只在**取数来源**与**出处标注**：

| `--source` | 取数位置 | 报告里的 `source_origin` | 能否作为 AC1 证据 |
| --- | --- | --- | --- |
| `fixture` | 仓库自带合成夹具 | `fixture` | 否 |
| `recorded` | `fixtures/recorded/*.SYNTHETIC.json` 本地合成回放 | `recorded` | 否 |
| `capture` | `scripts/live_capture.py` 落盘的真实响应（`--capture-dir`） | `live` | **是**（需 `complete=true` 且 `source=lingxing`） |
| `lingxing` | 现场直连领星开放平台 | `live` | **是** |

四条路都汇到 `scripts/source_data.py` 的 `load_source_data()`，返回同一套 `{batches, products, suppliers, invoices, provenance}`，之后统一交给 `scripts/sample50.py` 的 `build_ledger()` 与 `compare()`。所以判定行格式、一致率阈值（0.995）、分母口径（剔除 `source_errors`）在真实源上完全一致，变的只有 `source_origin` 与 `ac1_evidence_status`。

客户侧两步（在能访问 `openapi.lingxing.com:443` 且已开通开放接口的机器上）：

```bash
.venv/Scripts/python.exe scripts/live_capture.py --source lingxing --out captures/live
.venv/Scripts/python.exe scripts/sample50.py --source capture --capture-dir captures/live/<时间戳>
```

此时 `source_origin=live`、`counts_as_ac1_evidence=true`，一致率 ≥0.995 且无差异则 `ac1_evidence_status=verified_on_live_account`。未配凭据时明确报错退出码 2，不静默回退到合成数据（`tests/test_ac7_sample_source.py`）。

**本轮为什么没走到这一步**：`openapi.lingxing.com:443` 从交付方网络 TCP 不可达（`TcpTestSucceeded: False`、`curl` 退出码 28），且开放接口需付费套餐。原始命令与输出见 `docs/LIVE_EVIDENCE.md` 第 4 节。

---

## 3. 结余计算

### 3.1 公式的唯一实现

| 位置 | 内容 |
| --- | --- |
| `core/archive.py:24` | `def remaining(invoiced_total, used_amounts) -> str`：`remaining = invoiced_total − sum(used_by_batch)`，全程 `Decimal`，结果 `quantize(Decimal("0.01"))`（`core/archive.py:29`） |
| `core/archive.py:38` | `InvoiceUsage.use(batch_no, qty)`：先算剩余、超用直接抛 `OverUsed`，再累加本次耗用并返回新的剩余 |
| `core/archive.py:48` | `InvoiceUsage.remaining()` → 调 `remaining()` |
| `core/archive.py:51` | `recalc_stamp(at)`：每次回填留一个重算戳 |

### 3.2 回填写入点

| 位置 | 内容 |
| --- | --- |
| `core/module4.py:170` | `Module4Filler.fill_batch()`：本批次的写回入口 |
| `core/module4.py:199` | 写「耗用发票数量」 |
| `core/module4.py:200` | 写「发票剩余可用数量」= 票面数量 − **全部批次**累计耗用（跨批次共享同一张票） |
| `core/module4.py:201` | 写「结余」= 票面数量 − **本批次**累计耗用（本批次视角的余量） |
| `core/module4.py:138` | `_put()`：只在格子还空着时写入，避免与视觉补位抢同一格 |

两个余量口径刻意分开，且都由 `core/archive.py:24` 的 `remaining()` 算出，**没有任何一处另写减法**。

### 3.3 「结余」列在 146 行上的非空数

| 列 | 非空行数 | 行数合计 | 非空率 |
| --- | --- | --- | --- |
| 结余 | 96 | 146 | 0.6575 |
| 发票剩余可用数量 | 96 | 146 | 0.6575 |
| 归档共享链接 | 146 | 146 | 1.0000 |

明细行共 96 行，因此明细列的理论上限就是 96/146（0.6575）：50 个批次表头行只写批次号、发货单号、合同号、归档共享链接，不写明细列。「结余」**96/96 明细行全部有值**。

可证伪断言：

- `tests/test_module4_fill.py:49` `test_module4_balance_equals_invoiced_minus_batch_usage`：逐行断言「结余」== `remaining(票面数量, [本批次累计耗用])`；
- `tests/test_module4_fill.py:63` `test_module4_remaining_column_equals_invoiced_minus_all_usage`：逐行断言「发票剩余可用数量」== `remaining(票面数量, [全部批次累计耗用])`；
- `tests/test_module4_fill.py:96` `test_module4_usage_called_on_production_path`：断言 `remaining()` 确实被生产链路调用，不是只有测试在调。

---

## 4. 待补清单

### 4.1 怎么产生

| 位置 | 作用 |
| --- | --- |
| `core/checks.py:11` | `missing_issues()`：逐个必填列检查，空值产出一条 `code="missing_field"`（`core/checks.py:18`） |
| `core/checks.py:97` | `all_issues()`：必填 + HSCODE 格式 + 单价合理性 + 勾稽一次性合并，**报一条就停是不允许的** |
| `core/declare.py:180` | 回填每行时调用 `all_issues()`，问题挂到该批次结果上 |
| `core/checks.py:113` | `pending_list()`：按 `(batch_no, column, code)` 排序输出，业务可逐条补 |

### 4.2 示例输出

50 批次实跑共 **4 条**待补，字段名固定为 `batch_no / column / code / message`：

| batch_no | column | code | message |
| --- | --- | --- | --- |
| B2026-0011 | HSCODE | missing_field | 必填字段为空 |
| B2026-0022 | HSCODE | missing_field | 必填字段为空 |
| B2026-0033 | HSCODE | missing_field | 必填字段为空 |
| B2026-0044 | HSCODE | missing_field | 必填字段为空 |

这 4 条与第 1.2 节的 `source_errors` 一一对应：源数据缺 HSCODE → 回填时该格为空 → 自动进待补清单。**源数据缺陷既不计入一致率分母，又不会静默丢掉，而是变成一条可执行的待补任务。**

断言位置：`tests/test_ac1_fill_consistency.py:40` `test_ac1_missing_fields_become_pending_list`（断言清单非空、严格升序、每条恰含四个键）。

---

## 5. 与源系统比对的独立性

期望值不是引擎自己算的，而是 `scripts/reference_impl.py` 独立重算：它**不复用 `core/` 任何一行代码**，用 `fractions.Fraction` + 整数四舍五入把 2408 个格子再算一遍，写进 `fixtures/expected.json`。`scripts/sample50.py` 拿引擎输出与这份期望逐格比。两套实现同时错成一样的概率极低，这是「一致率 1.0」可信的来源。

`fixtures/expected.json` 里 23 列全有覆盖（`columns_covered` 长度 23），不存在「只比模块二那 16 列」的漏洞。

复现期望值：

```bash
.venv/Scripts/python.exe scripts/reference_impl.py --source recorded
```

实测末两行：`rows 146 cells 2408 columns 23 source_errors 4 -> fixtures/expected.json`，退出码 0。

---

## 6. 关联证据

| 内容 | 路径 |
| --- | --- |
| 抽检脚本（判定与报告） | `scripts/sample50.py` |
| 独立参考实现（期望值） | `scripts/reference_impl.py` |
| 抽检报告（机读） | `reports/sample50_report.json` |
| 期望值与源错误清单 | `fixtures/expected.json` |
| 结余公式 | `core/archive.py` |
| 结余回填写入点 | `core/module4.py` |
| 必填与待补清单 | `core/checks.py` |
| AC1 断言 | `tests/test_ac1_fill_consistency.py` |
| 结余断言 | `tests/test_module4_fill.py` |
| 数据来源口径断言 | `tests/test_ac7_sample_source.py` |
| 真实取数 / 写台账证据与复现步骤 | `docs/LIVE_EVIDENCE.md` |
| 抓取器（落盘请求与响应 + manifest） | `scripts/live_capture.py` |
| 六条验收的完整对照表 | `EVIDENCE.md` |