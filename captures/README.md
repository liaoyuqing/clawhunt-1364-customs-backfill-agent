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
| `dingtalk/20261126T174944Z/` | 真实钉钉开放平台 | **建表入口探测**（`--probe-create`）：8 条创建路径逐条实测，`complete=true`、`failure_count=0`、`denial_count=8`。用途仅为证明我方链路通，`environment=self_built_test_table`、`counts_as_customer_evidence=false`。 |

细节与复现方式见 `docs/LIVE_EVIDENCE.md`。
## 一个 `dingtalk/<时间戳>/` 目录里有什么

| 文件 | 内容 |
| --- | --- |
| `NN-<endpoint>.request.json` | 方法 / 路径 / query 名值（凭据类与 `operatorId` 打码 `***`）/ header 名值（凭据类打码）/ body 键名 + sha256 + 脱敏后的 body |
| `NN-<endpoint>.response.json` | 平台原始响应体（令牌响应体整段不落盘，只留键名、长度、sha256） |
| `NN-<endpoint>.failure.json` | 抓取步骤失败（网络不通 / 5xx / 非预期 4xx），`complete` 会被拉成 `false` |
| `NN-<endpoint>.denial.json` | **探测型**请求被平台明确拒绝（403 缺权限 / 400 参数错 / 404 接口不存在）。这是预期结果，记入 `denials[]`，**不影响 `complete`** |
| `manifest.json` | `origin=live`、`complete`、`replayable`、逐端点状态码 / sha256 / 记录条数、`failures[]`、`denials[]`、`derived`（含 `environment` 标注） |

`origin` 只会是 `live`：`core/sources.py` 的 `capture_payloads()` 会拒绝任何非 `live` 的目录，
合成数据无法从这条路径冒充真实抓取（见 `tests/test_ac7_sample_source.py`）。

`captures/live/` 是**默认的抓取输出目录，被 `.gitignore` 忽略**（客户环境可能含真实业务数据）；
要进仓库的证据请显式指定 `--out captures/dingtalk`（或另建目录）——本目录下现有的这些就是这么来的。