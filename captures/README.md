# captures/ —— 真实抓取落盘目录

这里放 `scripts/live_capture.py` 落盘的**真实**请求与响应（`captures/live/<UTC 时间戳>/`），
以及交付方在无凭据/无权限条件下仍能打到的真实平台应答（如本目录下的 `dingtalk/`）。

## 约定

- 每个目录都有一份 `manifest.json`，其中 `origin` 必须是 `live`；`core/sources.py` 的 `capture_payloads()`
  会强制校验这个字段，不是 `live` 直接拒绝，合成数据不可能从这里冒充真实抓取。
- `manifest.complete=false` 的目录（有端点失败）不允许当作一致率证据源；`source` 不是 `lingxing` 的
  目录（例如钉钉侧）也不允许——AC#1 只能用领星业务接口的抓取目录。
- 凭据一律不落盘：`Authorization` / `x-acs-dingtalk-access-token` / `appKey` / `appSecret` 在写文件前
  就被打码成 `***`；钉钉令牌响应体整段不写文件，只留键名与 sha256。
- 客户环境里抓到的目录可能含真实业务数据，是否入库由客户自己决定；本仓库不包含任何客户数据。

## 本目录现有内容

| 目录 | 来源 | 说明 |
| --- | --- | --- |
| `dingtalk/20261126T165132Z/` | 真实钉钉开放平台（`api.dingtalk.com`） | 令牌换发成功（HTTP 200）+ 两个端点 403 `Forbidden.AccessDenied.AccessTokenPermissionDenied`（缺 `Document.Workbook.Read`）。占位 workbook/sheet id，用于探测权限范围。 |
| `dingtalk/20261126T172559Z/` | 真实钉钉开放平台 | 权限开通后重抓：令牌 200 + 两个端点 400 `MissingoperatorId`（权限已过，卡参数）。 |
| `dingtalk/20261126T173159Z/` | 真实钉钉开放平台 | 带非法 `operatorId` 重抓：400 `paramError-operatorId`，值已打码 `***`，证明 query 真的拼上去了。 |
| `dingtalk/20261126T174944Z/` | 真实钉钉开放平台 | **建表入口探测（权限授予前）**：8 条创建路径逐条实测，`complete=true`、`failure_count=0`、`denial_count=8`（4 条 403 缺 scope + 1 条 404 接口不存在 + 3 条卡 operatorId）。用途仅为证明我方链路通，`environment=self_built_test_table`、`counts_as_customer_evidence=false`。 |
| `dingtalk/20261126T180011Z/` | 真实钉钉开放平台 | **建表入口探测（知识库权限补齐后重跑）**：`complete=false`、`failure_count=1`（`POST /v1.0/doc/workspaces/{假id}/docs` 稳定 500 `internalError`）、`denial_count=7`。用于**权限状态对照**：三条 wiki 入口由 403 变 `400 MissingoperatorId`，证明 `Wiki.Workspace.*` 与 `Document.WorkspaceDocument.Write` 已授予；`Contact.User.Read` 仍 403。`replayable=false`、`source=dingtalk`，不会被一致率计算使用。 |
| `dingtalk/20261126T190335Z/` | 真实钉钉开放平台 | **写一行前导零台账**（`B2026-0002` / HSCODE `0902100000`），8 个请求全 200，`complete=true`、`write_evidence.written == readback`、`match=true`、`range=A3:Z3`。证明 `keep_text()` 修好之后前导零能被真正写进去并读回一致。`environment=self_built_test_table`、`counts_as_customer_evidence=false`。 |
| `dingtalk/20261126T190624Z/` | 真实钉钉开放平台 | **前导零三写法对照探测**：9 个请求全 200，结论在 `manifest.derived.leading_zero_probe`——`platform_default` 读回 `902100000`（前导零丢失、`preserved=false`）、`apostrophe_text` 与 `formula_text` 均 `preserved=true`。这一份是「平台确实会吃前导零」的原始证据。同样 `counts_as_customer_evidence=false`。 |
| `dingtalk/20261126T185615Z/` | 真实钉钉开放平台 | **自建测试表格跑通写入**（9 个请求全 200，`complete=true`、`failure_count=0`、`denial_count=0`）：建表 → 列工作表 → 写表头 `A1:D1` → 按台账真路径写一行 `A2:Z2` → **回读逐格比对一致**（`["B2026-0001","BND-2S","桌面组合套装A","3924900000"]`）。`environment=self_built_test_table`、`counts_as_customer_evidence=false`：只证明我方链路通，不是客户台账，也不构成 AC#1 证据。 |

细节与复现方式见 `docs/LIVE_EVIDENCE.md`。
## 一个 `dingtalk/<时间戳>/` 目录里有什么

| 文件 | 内容 |
| --- | --- |
| `NN-<endpoint>.request.json` | 方法 / 路径 / query 名值（凭据类与 `operatorId` 打码 `***`）/ header 名值（凭据类打码）/ body 键名 + sha256 + 脱敏后的 body |
| `NN-<endpoint>.response.json` | 平台原始响应体（令牌响应体整段不落盘，只留键名、长度、sha256） |
| `NN-<endpoint>.failure.json` | 抓取步骤失败（网络不通 / 5xx / 非探测步骤的非预期 4xx），`complete` 会被拉成 `false`。**探测模式下 5xx 仍进这里**——平台内部错误不是「拒绝」，藏着不说才是问题 |
| `NN-<endpoint>.denial.json` | **探测型**请求被平台明确拒绝（403 缺权限 / 400 参数错 / 404 接口不存在）。这是预期结果，记入 `denials[]`，**不影响 `complete`** |
| `manifest.json` | `origin=live`、`complete`、`replayable`、逐端点状态码 / sha256 / 记录条数、`failures[]`、`denials[]`、`derived`（含 `environment`、`counts_as_customer_evidence`、`target`、`write_evidence`、`leading_zero_probe`） |

响应体是**平台原文照存**，不做裁剪：`20261126T185615Z/07-dingtalk_range.response.json` 有 1.8 MB，
因为读 `A1:Z300` 时平台连每个单元格的对齐、字体、颜色都一并回传（300 行 × 26 列 × 多个样式字段）。
这正是「原始响应」该有的样子——真要裁剪就会变成我们加工过的数据，别人没法核对。

`origin` 只会是 `live`：`core/sources.py` 的 `capture_payloads()` 会拒绝任何非 `live` 的目录，
合成数据无法从这条路径冒充真实抓取（见 `tests/test_ac7_sample_source.py`）。

`captures/live/` 是**默认的抓取输出目录，被 `.gitignore` 忽略**（客户环境可能含真实业务数据）；
要进仓库的证据请显式指定 `--out captures/dingtalk`（或另建目录）——本目录下现有的这些就是这么来的。