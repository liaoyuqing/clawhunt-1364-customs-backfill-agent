# 真实取数与写台账的运行证据（LIVE EVIDENCE）

本文件回答买方第 2 次驳回留下的两条意见：

1. [blocker] AC#1 的 `consistency_rate 1.000000` 是合成数据自比，要求真实源重跑；
2. [major] 缺「真的取过数、真的写过台账」的运行证据。

两条其实是同一件事的两面，本文件的处理方式是：**能真的跑的都跑了并把报文落盘，跑不了的把失败现场原样留证并写清为什么**。不美化、不把合成数据包装成真实抓取。

目录：

- 第 1 节 客户侧三步复现（拿到账号后一条命令产出证据）
- 第 2 节 真实证据长什么样（目录、manifest、报告字段）
- 第 3 节 领星开放接口的开通条件与账号要求
- 第 4 节 我方本机实测：领星 TCP 443 不可达（命令与原始输出）
- 第 5 节 钉钉侧真实证据（令牌换发成功 + 两个 scope 403 原文 + 拿到权限后的写入样例）
- 第 6 节 诚实声明：本轮哪些是 live、哪些不是

---

## 1. 客户侧三步复现

前提：机器能访问 `openapi.lingxing.com:443`，且领星开放接口已开通（条件见第 3 节）。

```powershell
cd <仓库根目录>

# 步骤 0（只做一次）：把凭据放进环境变量，不要写进任何文件
$env:LINGXING_BASE_URL = 'https://openapi.lingxing.com'
$env:LINGXING_APP_ID   = '<领星开放后台 API 信息页的 AppID>'
$env:LINGXING_APP_SECRET = '<同页的 AppSecret>'
$env:LINGXING_SID        = '<店铺 sid，货件列表必填>'
$env:LINGXING_START_DATE = '2026-01-01'   # 左闭
$env:LINGXING_END_DATE   = '2026-12-01'   # 右开

# 步骤 1：抓真实数据，逐次请求与原始响应落盘
.venv\Scripts\python.exe scripts\live_capture.py --source lingxing --out captures/live

# 步骤 2：用刚落盘的真实响应重算 50 批次逐字段一致率
.venv\Scripts\python.exe scripts\sample50.py --source capture --capture-dir captures/live/<时间戳>

# 步骤 3（可选）：确认报告口径
Get-Content reports\sample50_report.json | Select-String 'source_origin|ac1_evidence_status|consistency_rate'
```

也可以把凭据放在一个不在仓库里的文件里，用 `--env-file` 读进进程环境（脚本只读不打印、不回写）：

```powershell
.venv\Scripts\python.exe scripts\live_capture.py --source lingxing --out captures/live --env-file D:\secrets\.env.local
```

预期结果：`source_origin` 变成 `live`，`ac1_evidence_status` 变成 `verified_on_live_account`（一致率 ≥0.995 且无差异时）。**只有走到这一步，AC#1 才算在真实账号上验过。**

台账写回侧对应命令：

```powershell
$env:DINGTALK_CLIENT_ID     = '<钉钉应用 AppKey>'
$env:DINGTALK_CLIENT_SECRET = '<钉钉应用 AppSecret>'
$env:DINGTALK_WORKBOOK_ID   = '<工作簿 id：知识库节点 nodeId 或开放平台 workbookId>'
$env:DINGTALK_SHEET_ID      = '<工作表 id>'
$env:DINGTALK_OPERATOR_ID   = '<操作人 unionId：读区域与写区域都会先校验，缺失回 400 MissingoperatorId>'

.venv\Scripts\python.exe scripts\live_capture.py --source dingtalk --out captures/live/dingtalk --write-sample PROBE
```

---

## 2. 真实证据长什么样

### 2.1 目录结构

```text
captures/live/20261127T001530Z/
  01-lingxing_token.request.json       令牌请求（appSecret 打码为 ***）
  01-lingxing_token.response.json      令牌响应（token 整段不落盘，只留键名与 sha256）
  02-products.request.json             业务请求：方法 / 路径 / query / 头（Authorization 打码）/ body 键与 sha256
  02-products.response.json            平台原始响应体（整段原文，未加工）
  03-suppliers.request.json
  03-suppliers.response.json
  04-shipments.request.json
  04-shipments.response.json
  05-customs.request.json
  05-customs.response.json
  06-customs_detail.request.json
  06-customs_detail.response.json
  manifest.json                        逐条 endpoint 的汇总
```

### 2.2 `manifest.json` 的关键字段

| 字段 | 含义 |
| --- | --- |
| `origin` | `live` 表示真实抓取；回放入口强制校验这个字段，不是 `live` 直接拒绝（`core/sources.py` 的 `capture_payloads()`） |
| `source` | `lingxing` / `dingtalk`；钉钉的抓取目录不能拿来算 AC#1（也会被拒绝） |
| `complete` | 有没有端点失败；`false` 的目录不允许当一致率证据源 |
| `replayable` | 货件是否齐备；缺货件就没法生成台账行 |
| `endpoints[].requested_at_utc` | 每个请求的 UTC 时间 |
| `endpoints[].status` | HTTP 状态码 |
| `endpoints[].response_sha256` | 原始响应体的 sha256（改动一个字节就对不上） |
| `endpoints[].record_count` / `total` | 本次返回的记录条数与平台给的总数 |
| `failures[]` | 失败端点：状态码、错误原文、sha256 |
| `derived.required_scopes` | 平台回显的缺失权限（钉钉侧） |

### 2.3 报告里的对应字段（`reports/sample50_report.json`）

| 字段 | 说明 |
| --- | --- |
| `source` | `fixture` / `recorded` / `capture` / `lingxing` |
| `source_origin` | `live` / `recorded` / `fixture`，如实反映数据出处 |
| `source_host` | live 时是接口主机名；非 live 时是 `local files (no network)` |
| `source_fetched_at` | live 时是抓取时间（取 manifest 的 `created_at`） |
| `counts_as_ac1_evidence` | **只有 `origin=live` 才是 `true`** |
| `ac1_evidence_status` | `verified_on_live_account` / `live_source_below_threshold` / `unverified_on_live_account` |
| `reason` / `reproduce_steps` | 未验证时写明原因与客户侧三步复现 |

---

## 3. 领星开放接口的开通条件

这几条是拿到账号之前绕不过去的前提，写清楚以免被误当成「代码没写完」：

1. **开放接口不是免费版能力，需要付费套餐。** 领星开放平台（OpenAPI）是面向付费客户的增值能力，免费版 / 试用版没有开放接口权限，也没有公开的公共沙箱环境可供第三方自测。
2. **AppID / AppSecret 的申请路径**：用超级管理员账号登录领星 ERP → 设置 → 业务配置 → 全局 → 开放接口 → 填写接收通知的邮箱 → 进入开放后台 → 「API 信息」页查看 AppID / AppSecret（AppSecret 可重置）。
3. **必须配置 IP 白名单**：同一页的 IP 白名单只接受公网 IP（不支持域名），请求源 IP 不在白名单内会被拒绝。
   - 我方当前出口 IP 实测为 `36.148.244.149`；家宽 / 移动网络出口会变，变化后需要在开放后台改白名单。
4. **appId 必须是 16 / 24 / 32 字节**：签名第 3 步用 appId 作为 AES 密钥，长度不合法时平台一定判签名错误，客户端已提前报错（见 `tests/test_protocol_conformance.py`）。
5. **需要账号内已有数据**：货件列表接口必填 `sid` 与日期区间；报关资料明细需要账号里存在报关单。空账号跑出来是 0 条，那不是失败，但也不能算证据。

参考文档（无需密钥即可访问）：

- 接入指南（含签名规则、限流）：`https://apidoc.lingxing.com/new/guides/new-instructions/`
- 申请 AppID：`https://apidoc.lingxing.com/new/guides/app-id/`
- 取令牌：`https://apidoc.lingxing.com/new/api-docs/Authorization/GetToken/`
- 产品列表：`https://apidoc.lingxing.com/new/api-docs/Product/ProductLists/`
- 供应商：`https://apidoc.lingxing.com/new/api-docs/Purchase/Supplier/`
- 货件列表：`https://apidoc.lingxing.com/new/api-docs/FBA/FBAShipmentList/`

---

## 4. 我方本机实测：领星 TCP 443 不可达

为什么这一轮的真实取数停在「抓不起」这一步，下面是原始命令与输出，评审可以自己复跑。

```powershell
PS> Resolve-DnsName openapi.lingxing.com -Type A
Name                 IPAddress
----                 ---------
openapi.lingxing.com 114.132.29.163

PS> Test-NetConnection openapi.lingxing.com -Port 443 -InformationLevel Detailed
ComputerName     : openapi.lingxing.com
RemoteAddress    : 114.132.29.163
TcpTestSucceeded : False          # <- TCP 443 握手不成

PS> curl.exe -sS -m 20 -o NUL -w 'api http=%{http_code} time=%{time_total}' -X POST https://openapi.lingxing.com/api/auth-server/oauth/access-token
curl: (28) Connection timed out after 20008 milliseconds
api http=000 time=20.010002

PS> curl.exe -sS -m 25 -o NUL -w 'apidoc http=%{http_code}' https://apidoc.lingxing.com/new/
apidoc http=200          # <- 文档站可达，说明不是整体断网
```

同一台机器对钉钉是通的（对照，排除本机网络整体故障）：

```powershell
PS> Test-NetConnection api.dingtalk.com -Port 443 -InformationLevel Quiet
True
```

结论：`openapi.lingxing.com:443` 从交付方网络 TCP 不可达（DNS 解析正常，说明是链路 / 出口侧限制或对方对该来源的限制），而 `apidoc.lingxing.com` 与 `api.dingtalk.com` 都可达。所以本轮无法在交付方网络产出领星侧的真实响应；这也正是第 1 节把复现步骤写成「客户侧一条命令」的原因——在客户网络里这一步是通的。

无凭据时的行为（不创建半成品目录、不静默回退）：

```powershell
PS> .venv\Scripts\python.exe scripts\live_capture.py --source lingxing --out captures/live
无法开始抓取：缺少环境变量：LINGXING_APP_ID、LINGXING_APP_SECRET
提示：把 LINGXING_APP_ID / LINGXING_APP_SECRET 放进环境变量，或用 --env-file 指向 .env.local
PS> $LASTEXITCODE
2
PS> Test-Path captures/live
False            # <- 失败不会留下空目录或占位文件
```

网络不通时的行为：把失败的那一次请求写进 `failures[]`（含错误原文），`manifest.complete=false`，退出码 3，绝不把失败伪装成成功。

---

## 5. 钉钉侧真实证据（本轮实际跑通的部分）

钉钉是本轮唯一能从交付方网络真实打到平台的接口。凭据只存在于交付方的本地文件，**不进仓库、不进文档**；下面所有报文都已脱敏（appKey / appSecret / accessToken 一律打码）。

### 5.1 令牌换发：成功

请求（仓库内实测目录 `captures/dingtalk/20261126T165132Z/01-dingtalk_token.request.json`，即上文 `--out captures/live/dingtalk` 的产物）：

```json
{
  "seq": 1,
  "endpoint": "/v1.0/oauth2/accessToken",
  "method": "POST",
  "url": "https://api.dingtalk.com/v1.0/oauth2/accessToken",
  "headers": { "Accept": "application/json", "Content-Type": "application/json" },
  "body_keys": ["appKey", "appSecret"],
  "body_sha256": "5bf2a214e5a45b94c9b68840dff5edb7daed32e3b9031056c29e5ff897ac0f61",
  "body_bytes": 115,
  "body": { "appKey": "***", "appSecret": "***" }
}
```

响应（`01-dingtalk_token.response.json`，令牌本身不落盘）：

```json
{
  "_redacted": "token response body is never persisted",
  "_keys": ["accessToken", "expireIn"],
  "_text_sha256": "da2d6af2629cd424b30d35dad6929efc6348fad52372cfe4643f18a1008de1de",
  "_text_length": 66
}
```

`manifest.json` 里这一条记为 `status 200 / ok true / origin live`。也就是说：**钉钉应用的 AppKey/AppSecret 有效，等价于「能拿到 7200 秒有效的 accessToken」这一步已经真实验证过**。

### 5.2 权限开通前：403 缺权限（原文，已过时但保留作对照）

```json
{
  "_transport_error": "http 403 from https://api.dingtalk.com/v1.0/doc/workbooks/PLACEHOLDER-WORKBOOK-ID/sheets",
  "status": 403,
  "body": {
    "code": "Forbidden.AccessDenied.AccessTokenPermissionDenied",
    "requestid": "01A218C4-F9ED-73E9-9823-56AFD5F8DEB1",
    "message": "应用尚未开通所需的权限：[Document.Workbook.Read]，点击链接申请并开通即可：https://open-dev.dingtalk.com/appscope/apply?content=ding***az%23Document.Workbook.Read",
    "accessdenieddetail": { "requiredScopes": ["Document.Workbook.Read"] }
  }
}
```

读区域（`.../sheets/PLACEHOLDER-SHEET-ID/ranges/A1%3AZ3`）返回同样的 403、同一缺失范围，`requestid` 为 `01A218C4-FAAE-722D-9354-E9DDC087DAA1`。

另外单独探过 `GET /v1.0/contact/users/me`，返回 403 缺 `Contact.User.Read`（`requestid 01A218A9-02F6-7A93-895D-C3DFD7A586B6`）。这个权限只影响「自动取操作人 unionId」，不影响写入本身。

**本应用当前缺的权限（平台原文）：**

| Scope | 用途 | 申请入口 |
| --- | --- | --- |
| `Document.Workbook.Read` | 读工作表列表与区域 | `https://open-dev.dingtalk.com/appscope/apply?content=<AppKey>%23Document.Workbook.Read` |
| `Document.Workbook.Write` | 写单元格（回填台账必需） | 同页，把 scope 换成 `Document.Workbook.Write` |
| `Contact.User.Read`（可选） | 自动取操作人 unionId | 同上 |

> 注意：`PLACEHOLDER-*` 是占位 id。占位 id 下的 403 说明的是「应用缺权限」，不能推断表不存在；等权限到位且配上真实 `workbookId/sheetId` 后，同一条命令就会返回真正的表结构与单元格。

### 5.2.1 权限开通后：403 变 400，缺的从「权限」变成「operatorId」（真实原文）

客户于 2026-11-27 给应用勾上了 `Document.Workbook.Read/Write`。同一天用**同一条命令**重抓，平台返回体变了：

```json
{
  "_transport_error": "http 400 from https://api.dingtalk.com/v1.0/doc/workbooks/PLACEHOLDER-WORKBOOK-ID/sheets",
  "status": 400,
  "body": {
    "code": "MissingoperatorId",
    "requestid": "01A218E4-8680-70BE-AD65-4BD65F88D7DA",
    "message": "operatorId is mandatory for this action."
  }
}
```

读区域（`.../sheets/PLACEHOLDER-SHEET-ID/ranges/A1%3AZ3`）同样返回 400 `MissingoperatorId`，`requestid 01A218E4-876B-7230-98CB-B7F2FF99E4FB`。证据目录 `captures/dingtalk/20261126T172559Z/`（7 个文件 + manifest，`origin live`）。

再带一个故意非法的 `operatorId` 重跑一遍，用来证明「query 真的拼上去了、平台真的在校验值」：

```json
{
  "_transport_error": "http 400 from https://api.dingtalk.com/v1.0/doc/workbooks/PLACEHOLDER-WORKBOOK-ID/sheets?operatorId=***",
  "status": 400,
  "body": {
    "requestid": "01A218EA-04AB-7187-BA1E-869BE17558FD",
    "code": "invalidRequest.inputArgs.invalid",
    "message": "paramError-operatorId"
  }
}
```

证据目录 `captures/dingtalk/20261126T173159Z/`。注意 `_transport_error` 里的 `operatorId` 值已打码为 `***`（unionId 属个人信息，不进证据目录）——请求文件里的 `query` 数组同样只保留参数名。

**这条对照本身就是「权限已到位」的证据**：权限没开通时平台在 scope 校验这一步就 403 拒绝了，根本走不到参数校验；现在能走到 `operatorId` 校验，说明 `Document.Workbook.Read` 已授予。

接下来只剩参数，官方要求 `operatorId` 是**操作人的 unionId**（不是手机号、不是 userId）：

| 再传什么 | 平台返回 | 说明 |
| --- | --- | --- |
| 不传 | 400 `MissingoperatorId` / `operatorId is mandatory for this action.` | 见上 |
| 传 `PROBE` | 400 `invalidRequest.inputArgs.invalid` / `paramError-operatorId` | 串太短 |
| 传 32 位十六进制 / 32 位小写字母 / 带连字符 UUID | 同上 `paramError-operatorId` | 平台做的是值校验，编不出来 |

所以这一格**必须由客户提供真实 unionId**，猜不出来也造不出来。

### 5.2.2 三个 id：已全部取到（不靠客户提供）

客户补开 `qyapi_get_department_list` + `qyapi_get_member` 之后，三个 id 由本仓库脚本自己查了出来，
**没有让客户手工抄任何 id**。取法与结果（全部真实请求 200）：

| 变量 | 怎么取到的 | 值 |
| --- | --- | --- |
| `DINGTALK_OPERATOR_ID` | 老版 oapi：`gettoken` → `topapi/v2/department/listsub` 递归部门 → `topapi/user/listid` → `topapi/v2/user/get` 读 `unionid` | `z4gl***iEiE`（操作人 unionId；完整值只在 `.env.local`，**不入库、文档不写全**） |
| `DINGTALK_WORKBOOK_ID` | `POST /v1.0/doc/workspaces/{workspaceId}/docs` 建一张 `docType=WORKBOOK` 的表格，返回 `docKey` | `eonavxLprD13aqXx`（`dentryUuid` 也可用；`nodeId` **不行**，实测 404 `uuid not exist`） |
| `DINGTALK_SHEET_ID` | `GET /v1.0/doc/workbooks/{docKey}/sheets` 返回的 `id` | `kgqie6hm`（表名 `Sheet1`） |

**knowledge 空间是怎么定位的**：`GET /v2.0/wiki/mineWorkspaces?operatorId=<unionId>` → 200，个人空间
`workspaceId=Kw8lASMkmzyKLaE5`（`type=PERSONAL`，`permissionRole=OWNER`）；
`GET /v2.0/wiki/workspaces` 也 200 但返回 `{}`（该企业没有别的知识库）。

**踩到的两个坑，写下来免得后人重踩**：

1. `nodeId`（`qWXwP76kALZZgm31`）看着最像 workbookId，但拿它列工作表是 **404 `invalidRequest.resource.notFound`「uuid not exist」**；
   `docKey` 与 `dentryUuid` 都返回 200。所以「文档里有三种 id」这句话是对的，但只有两种能用。
2. `Contact.User.Read` **依然 403**（`GET /v1.0/contact/users/me`），所以走新版接口自动取 unionId 的路仍然不通；
   能取到是因为客户开了老版 oapi 的两个通讯录权限。这两条路取到的是同一个值，但要求的权限不同。

配齐后一条命令就能产出真实写入证据（下面的 5.4 节就是这一次的真实落盘）：

```powershell
python scripts/live_capture.py --source dingtalk --env-file <项目下 .env.local 的绝对路径> `
    --workbook-id eonavxLprD13aqXx --sheet-id kgqie6hm --operator-id <unionId> `
    --self-built-table --write-ledger-row B2026-0001
```

### 5.2.3 自建测试表格：已经建出来了，写进去并读回一致

需求方给的思路是：**我方自己建一张表，跑通一次写入，只为证明链路通，不当客户环境证据**。
这条已经跑完了：表格由本仓库脚本自己建（`POST /v1.0/doc/workspaces/{workspaceId}/docs`，`docType=WORKBOOK`），
写入走的是交付里的同一条 `write_row()` 路径，写完立刻回读比对。

| 环节 | 结果 |
| --- | --- |
| 建表格文档 | **200**，`docKey=eonavxLprD13aqXx`、`dentryUuid=QOG9lyrgJPvDGp12TPvN5DZxWzN67Mw4`、`nodeId=qWXwP76kALZZgm31` |
| 列工作表 | **200**，`[{"name":"Sheet1","id":"kgqie6hm"}]` |
| 写表头 | **200**，`A1:D1` ← `["批次号","SKU","产品中文报关名","HSCODE"]` |
| 写台账行 | **200**，`A2:Z2`（`A2:D2` 四列有值，其余空） |
| 回读校验 | `["B2026-0001","BND-2S","桌面组合套装A","3924900000"]` == 写入值，`match=true` |

落盘目录：`captures/dingtalk/20261126T185615Z/`（9 个请求 / 18 个文件 + manifest，`origin=live`、`complete=true`、
`failure_count=0`、`denial_count=0`、`environment=self_built_test_table`、`counts_as_customer_evidence=false`）。

复现命令（把 unionId 从 `.env.local` 注入即可，不需要任何手工抄写）：

```powershell
python scripts/live_capture.py --source dingtalk --out captures\dingtalk `
    --env-file <项目下 .env.local 的绝对路径> `
    --workbook-id eonavxLprD13aqXx --sheet-id kgqie6hm `
    --self-built-table --write-ledger-row B2026-0001
```

**这张自建表能证明什么、不能证明什么**（写在前面，免得被误读）：

- 能证明：我方客户端对钉钉开放平台的真实鉴权、真实请求构造、真实建表、真实 PUT 写入与**写入后回读比对**，端到端跑得通。
- 不能证明：任何与客户真实台账有关的事。它不是客户环境，`counts_as_customer_evidence` 恒为 `false`，
  也**不构成 AC#1 的一致率证据**（AC#1 只能用领星业务接口的真实抓取目录）。
- 这一格与买方说的「缺真的写过台账」**只差环境**：同一条代码路径、同一套报文、同样的回读校验，
  换成客户自己的 workbookId / sheetId 就是客户台账；现在缺的是客户那张表的两个 id，不是能力。

### 5.3 自建表格探测：官方文档里的创建入口逐条打了一遍（真实原文）

这一节是 5.2.3 那张表的取证过程。**环境标注：`environment=self_built_test_table`、`counts_as_customer_evidence=false`**——
它是「我方能打通到钉钉开放平台」的链路证据，不是客户台账证据。

复现命令（每条都真的发出去，成功和拒绝都按原文落盘）：

```powershell
python scripts/live_capture.py --source dingtalk --out captures\dingtalk `
    --env-file <项目下 .env.local 的绝对路径> --probe-create
```

落盘目录：`captures/dingtalk/20261126T174944Z/`（**第一次探测，权限授予前**），`origin=live`、`complete=true`、
`failure_count=0`、`denial_count=8`。**为什么拒绝记在 `denials[]` 而不是 `failures[]`**：探测的目的就是「问平台能不能建」，
「不能」是答案不是故障；记成 failure 会让 `complete` 变 false，反而盖住探测结论。

| # | 入口（官方文档里的写法） | 结果 | 原文要点 |
| --- | --- | --- | --- |
| 02 | `GET /v2.0/wiki/workspaces` 列知识库 | 403 | `requiredScopes=["Wiki.Workspace.Read"]` |
| 03 | `GET /v2.0/wiki/mineWorkspaces` 我的文档 | 403 | `requiredScopes=["Wiki.Workspace.Read"]` |
| 04 | `POST /v2.0/wiki/workspaces` 建知识库 | 403 | `requiredScopes=["Wiki.Workspace.Write"]` |
| 05 | `POST /v1.0/doc/workspaces/{workspaceId}/docs` body `docType=WORKBOOK` | 403 | `requiredScopes=["Document.WorkspaceDocument.Write"]`，requestid `01A218FA-45A7-77EC-9F7B-6EF2BDEEFED4` |
| 06 | `POST /v1.0/doc/workbooks` 直接建工作簿 | **404** | `InvalidAction.NotFound`「Specified api is not found」——**这个接口根本不存在** |
| 07 | `POST /v1.0/drive/spaces` 建云盘空间 | 403 | `requiredScopes=["Drive.Space.Write"]` |
| 08 | `POST /v1.0/drive/spaces/{id}/files` 云盘里建文件 | 403 | `requiredScopes=["Drive.File.Write"]` |
| 09 | `GET /v1.0/contact/users/me` 读当前用户（想顺手拿 unionId） | 403 | `requiredScopes=["Contact.User.Read"]` |

#### 5.3.1 知识库权限开通后重跑：403 全部变 400，权限这一层已经过了

客户把知识库相关权限补齐后，同一个命令重跑一遍，落盘在 `captures/dingtalk/20261126T180011Z/`
（`origin=live`、`environment=self_built_test_table`、`replayable=false`）。同一批入口的应答变了：

| # | 入口 | 权限前 | 权限后 | 说明 |
| --- | --- | --- | --- | --- |
| 02 | `GET /v2.0/wiki/workspaces` | 403 `Wiki.Workspace.Read` | **400 `MissingoperatorId`**「operatorId is mandatory for this action.」 | scope 已授予，改卡参数 |
| 03 | `GET /v2.0/wiki/mineWorkspaces` | 403 `Wiki.Workspace.Read` | **400 `MissingoperatorId`** | 同上 |
| 04 | `POST /v2.0/wiki/workspaces` | 403 `Wiki.Workspace.Write` | **400 `MissingoperatorId`** | 同上 |
| 05 | `POST /v1.0/doc/workspaces/{id}/docs` | 403 `Document.WorkspaceDocument.Write` | **500 `internalError`**「系统内部错误」 | scope 已过；假 workspaceId 打到内部异常 |
| 06 | `POST /v1.0/doc/workbooks` | 404 接口不存在 | 404 接口不存在 | 与权限无关，这个接口本来就没有 |
| 07/08 | `POST /v1.0/drive/spaces` 及建文件 | 403 `Drive.Space.Write` / `Drive.File.Write` | 仍 403 | 云盘系权限**没有**申请，也没必要——建表走知识库更直接 |
| 09 | `GET /v1.0/contact/users/me` | 403 `Contact.User.Read` | 仍 403 | 通讯录权限**没有**授予，所以 unionId 仍只能人工给 |

**怎么读这组对照**：「403 变 400」是权限到位的**判据**——平台先校验 scope，scope 不过直接 403 并回
`requiredScopes`；能走到参数校验（`MissingoperatorId` / `paramError-operatorId`）说明 scope 校验已经通过。
所以现在**缺的不再是权限，而是三个 id**。

两处需要说清的细节：

1. **第 05 条为什么是 500 而不是 400**：我用非 ID 形态的 `PROBE-WORKSPACE-ID` 去建文档，平台没有先做参数校验，
   直接抛内部错误。同一请求连打 3 次、再换 32 位大小写两种十六进制 ID 与 base64 风格 operator 各打 2 次，**累计 5 次全部稳定 500**，
   requestid 各异（`01A21904-BDE4-747A-A452-330345C885F2` / `-BEAB-700D-B9AA-A634640B354D` / `-BF8E-7E19-8FA2-1511DEDD55F9`），
   所以不是偶发网络抖动，是「假 workspaceId」触发的服务端异常。反过来，同一入口**不传** operatorId 时稳定 400 `MissingoperatorId`、
   传空 body 时稳定 400 `Missingbody`，说明这条路由本身是活的、参数校验顺序也正常。
2. **这一组的 `complete=false` 是如实记录，不是抓取失败**：500 不属于「平台明确拒绝（4xx）」，
   所以按口径记进 `failures[]` 而不是 `denials[]`，`failure_count=1` 把 `complete` 拉成 false。
   这组目录**只用于权限状态对照，不会被任何一致率计算使用**（`replayable=false`、`source=dingtalk`，`capture_payloads()` 两道都会拒）。

**建表的四条路，权限这一层已经全过了，最后都堵在同一个地方**：

| 路 | 权限前的堵点 | 权限到位后 | 结论 |
| --- | --- | --- | --- |
| 知识库 → 建表格文档 | 缺 `Wiki.Workspace.Write` + `Document.WorkspaceDocument.Write`（403） | 两条 scope 均已过：列库/建库返回 `400 MissingoperatorId`，建文档返回 500（假 workspaceId） | **只差真实 unionId + 一个真实 workspaceId** |
| 云盘 → 建文件 | 缺 `Drive.Space.Write` + `Drive.File.Write`（403） | 仍 403（这两个权限没申请，也不需要） | 不用这条路 |
| 直接 POST 工作簿 | **接口不存在**（404 `InvalidAction.NotFound`） | 仍 404 | 与权限无关，官方就没这个接口 |
| 已有表格下建工作表 | scope 已有，卡 `operatorId` | 仍卡 `operatorId` | **最接近成功**：只差真实 unionId + workbookId |

一句话：**权限已经买齐了，剩下的是三个 id**（operatorId / workbookId / sheetId）。

> 后续进展（同一天）：三个 id 已由本仓库脚本自查取得，并据此把表格建出来、写入并回读校验通过——
> 见 5.2.2 节（怎么取）与 5.2.3 节（跑通结果）。本节保留的是「权限刚到位那一刻」的原始状态，作对照用。

**为什么不能自己造 unionId**：平台对 `operatorId` 做**值校验**，不是随便填个字符串就能过。实测（全部只读）：

| 传入的 operatorId | 平台返回 |
| --- | --- |
| 不传 | `400 MissingoperatorId`「operatorId is mandatory for this action.」 |
| `PROBE` | `400 invalidRequest.inputArgs.invalid`「paramError-operatorId」 |
| 32 位十六进制 / 32 位大写十六进制 / 32 位小写字母 / 20 字符 base64 风格 / 44 字符 base64 / 带连字符 UUID | 同上，全部 `paramError-operatorId` |
| 放 header（`x-acs-dingtalk-operator-id` 或 `operatorId`） | 仍 `400 MissingoperatorId`——**header 不认，只认 query** |
| 放 body（`PUT .../ranges/A1:A1` body 里带 `operatorId`） | `400 paramError-operatorId`——**body 不认** |
| query + body 同时传 | `paramError-operatorId` |

「自己去查 unionId」的路也确认堵死：`GET /v1.0/contact/users/me` 与 `/contact/users/{userId}` 都要
`Contact.User.Read`；`contact/organizations/departments`、`empOrgInfo`、`unionId/cvt` 返回
`404 InvalidAction.NotFound`（不存在）；老版 `oapi` 的 `topapi/v2/user/get`、`topapi/user/listid` 返回
`errcode=88 subcode=60011` 缺 `qyapi_get_member`，`topapi/v2/department/listsub` 缺 `qyapi_get_department_list`。
**结论：unionId 只能人工给，工具不去猜。**

---

### 5.4 写回样例：离线报文形态 + 真实跑通后的实际请求

官方写单元格接口：`PUT /v1.0/doc/workbooks/{workbookId}/sheets/{sheetId}/ranges/{rangeAddress}?operatorId={unionId}`，body `{"values": [[...]]}`，头 `x-acs-dingtalk-access-token: <accessToken>`，需要 `Document.Workbook.Write`。

本仓库 `core/dingtalk_client.py` 的 `write_row()` 就是按这个形态实现的：先 `GET .../ranges/A1:Z1` 读表头定位列序，再 `GET .../ranges/A1:Z300` 按第一列找同行号（找不到就追加到最后一行下一行），最后 `PUT` 那一行。

离线验证过真实报文形态（回放器，不用真账号）：

```text
request GET  https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A1%3AZ1    headers={"Accept":"application/json","x-acs-dingtalk-access-token":"***"}
request GET  https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A1%3AZ300  headers={"Accept":"application/json","x-acs-dingtalk-access-token":"***"}
request PUT  https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A3%3AD3?operatorId=OPERATOR-SYNTHETIC-NOT-REAL  body=['values']
write_ok sheetId=SHEET-SYNTHETIC-NOT-REAL range=A3:D3
```

上面这段是**离线回放**（`recorded_smoke.py --source recorded`），用来说明报文形态。

真实跑通的那一次是 `captures/dingtalk/20261126T185615Z/`，9 个请求全部 200，顺序与上文一致：

```text
01 POST /v1.0/oauth2/accessToken                                                -> 200（响应体不落盘，只留 sha256）
02 GET  /v1.0/doc/workbooks/eonavxLprD13aqXx/sheets                             -> 200  [{"name":"Sheet1","id":"kgqie6hm"}]
03 GET  .../sheets/kgqie6hm/ranges/A1%3AZ3                                      -> 200  （写前巡检，全空）
04 GET  .../sheets/kgqie6hm/ranges/A1%3AD1                                      -> 200  （读表头，此时是上一轮探测残留）
05 PUT  .../sheets/kgqie6hm/ranges/A1%3AD1?operatorId=***                       -> 200  （把表头写正：批次号/SKU/产品中文报关名/HSCODE）
06 GET  .../sheets/kgqie6hm/ranges/A1%3AZ1                                      -> 200  （读表头定位列序）
07 GET  .../sheets/kgqie6hm/ranges/A1%3AZ300                                    -> 200  （读现有行定位行号；空表返回 300 行空串）
08 PUT  .../sheets/kgqie6hm/ranges/A2%3AZ2?operatorId=***                       -> 200  （PUT 整行）
09 GET  .../sheets/kgqie6hm/ranges/A2%3AZ2                                      -> 200  （回读：["B2026-0001","BND-2S","桌面组合套装A","3924900000"]）
```

第 07 步顺带修掉了一个真 bug：旧实现用 `len(读回行数)+1` 算追加行号，而读 `A1:Z300` 时空表也会回 300 行，
于是台账被写到了第 **301** 行——接口 200、回读也「一致」（读到的是空串），但表里平白多出 298 行空白。
现在改成按「最后一条有内容的行」+1，空表就落在第 2 行，并且有两条单测钉住（`test_dingtalk_write_row_appends_after_last_used_row_not_after_padding`、`..._on_empty_sheet_starts_at_row_two`）。

---

## 6. 诚实声明：本轮哪些是 live、哪些不是

| 项目 | 本轮状态 | 依据 |
| --- | --- | --- |
| 领星客户端协议（签名 / 路由 / 头 / 分页 / 限流） | 已按官方文档对齐，有协议一致性测试 | `tests/test_protocol_conformance.py`（19 条） |
| 领星真实响应 | **未取得**：出口网络 TCP 443 不可达 + 开放接口需付费套餐 | 第 4 节原始输出 |
| 领星抓取器（live_capture） | 已实现，无凭据退出码 2、不通退出码 3、不落半成品 | 第 4 节末尾两条实测 |
| 钉钉令牌换发 | **真实成功**（HTTP 200，token 长度 32） | 5.1 节 |
| 钉钉读表 / 读区域 | **真实打到平台**；权限开通前 403（缺 Document.Workbook.Read），开通后变 400（缺 operatorId），证明 scope 已到位 | 5.2、5.2.1 节原文 |
| 钉钉写台账（客户表） | **未取得**：我方已能自取三个 id 并跑通写入，但客户未提供其真实台账的 workbookId / sheetId，所以写的仍是我方自建表 | 5.2.3 节 |
| 钉钉「能不能建出表」 | **真实打到平台**：8 个创建入口逐条实测。权限授予前 4 条 403 缺 scope、1 条 404 接口不存在；**知识库权限补齐后重跑，3 条 403 全部变 `400 MissingoperatorId`（scope 校验已通过）**，剩余硬阻塞只有真实 unionId | 5.3、5.3.1 节 + `captures/dingtalk/20261126T174944Z/`（权限前）、`captures/dingtalk/20261126T180011Z/`（权限后） |
| 自建表格写入 | **真实跑通**：建表 → 列工作表 → 写表头 → 写台账行 → 回读逐格比对一致，9 个请求全 200。`environment=self_built_test_table`、`counts_as_customer_evidence=false`——只证明我方链路通，**不构成客户环境证据，也不构成 AC#1 证据** | 5.2.3、5.4 节 + `captures/dingtalk/20261126T185615Z/` |
| 钉钉权限授予状态 | **已核准**：`Wiki.Workspace.Read/Write`、`Document.WorkspaceDocument.Write`、`Document.Workbook.Read/Write`、`qyapi_get_member`、`qyapi_get_department_list` 均已到位；`Contact.User.Read`、`Drive.*` 未授予（不影响任何一条已跑通的路径） | 5.3.1 节 403→400 对照 + 5.2.2 节取 id 实录 |
| AC#1 一致率 1.000000 | 来自合成源，只证明「回填引擎与独立参考实现自洽」，**不算 AC#1 达标证据** | 报告字段 `counts_as_ac1_evidence=false`、`ac1_evidence_status=unverified_on_live_account` |

一句话总结：**协议层与流水线是真的，钉钉的鉴权、建表、写入与回读比对也都是真的（我方自建表已跑通）**；
唯一还空着的是「客户真实台账 + 领星真实业务数据的一致率」这一格——它需要客户侧的账号、网络与一张表，
第 1 节与 5.2.3 节分别给出了拿到之后一条命令就能填上的路径，而不是一句「请相信我们」。