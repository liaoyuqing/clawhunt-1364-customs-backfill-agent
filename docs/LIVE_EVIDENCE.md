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
$env:DINGTALK_OPERATOR_ID   = '<操作人 unionId，写接口的 operatorId 必填>'

.venv\Scripts\python.exe scripts\live_capture.py --source dingtalk --out captures/live/dingtalk
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

### 5.2 读工作表与读区域：403 缺权限（原文）

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

### 5.3 拿到权限后的写回样例（本仓库已实现，等权限即可跑）

官方写单元格接口：`PUT /v1.0/doc/workbooks/{workbookId}/sheets/{sheetId}/ranges/{rangeAddress}?operatorId={unionId}`，body `{"values": [[...]]}`，头 `x-acs-dingtalk-access-token: <accessToken>`，需要 `Document.Workbook.Write`。

本仓库 `core/dingtalk_client.py` 的 `write_row()` 就是按这个形态实现的：先 `GET .../ranges/A1:Z1` 读表头定位列序，再 `GET .../ranges/A1:Z300` 按第一列找同行号（找不到就追加到最后一行下一行），最后 `PUT` 那一行。

离线验证过真实报文形态（回放器，不用真账号）：

```text
request GET  https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A1%3AZ1    headers={"Accept":"application/json","x-acs-dingtalk-access-token":"***"}
request GET  https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A1%3AZ300  headers={"Accept":"application/json","x-acs-dingtalk-access-token":"***"}
request PUT  https://dingtalk.example.invalid/v1.0/doc/workbooks/BOOK-SYNTHETIC-NOT-REAL/sheets/SHEET-SYNTHETIC-NOT-REAL/ranges/A3%3AD3?operatorId=OPERATOR-SYNTHETIC-NOT-REAL  body=['values']
write_ok sheetId=SHEET-SYNTHETIC-NOT-REAL range=A3:D3
```

权限到位后，把 `https://dingtalk.example.invalid` 换成 `https://api.dingtalk.com`、把 `*-SYNTHETIC-NOT-REAL` 换成真实 id，上面三条请求就是真实请求；用 `scripts/live_capture.py --source dingtalk` 会把它们连同响应原文一起落盘。

---

## 6. 诚实声明：本轮哪些是 live、哪些不是

| 项目 | 本轮状态 | 依据 |
| --- | --- | --- |
| 领星客户端协议（签名 / 路由 / 头 / 分页 / 限流） | 已按官方文档对齐，有协议一致性测试 | `tests/test_protocol_conformance.py`（19 条） |
| 领星真实响应 | **未取得**：出口网络 TCP 443 不可达 + 开放接口需付费套餐 | 第 4 节原始输出 |
| 领星抓取器（live_capture） | 已实现，无凭据退出码 2、不通退出码 3、不落半成品 | 第 4 节末尾两条实测 |
| 钉钉令牌换发 | **真实成功**（HTTP 200，token 长度 32） | 5.1 节 |
| 钉钉读表 / 读区域 | **真实打到平台**，被 403 挡住（缺 Document.Workbook.Read） | 5.2 节原文 |
| 钉钉写台账 | **未取得**：缺 Document.Workbook.Write + 未提供 workbookId/sheetId/operatorId | 5.2、5.3 节 |
| AC#1 一致率 1.000000 | 来自合成源，只证明「回填引擎与独立参考实现自洽」，**不算 AC#1 达标证据** | 报告字段 `counts_as_ac1_evidence=false`、`ac1_evidence_status=unverified_on_live_account` |

一句话总结：**协议层与流水线是真的，钉钉的鉴权是真的，但「真实业务数据的一致率」这一格还空着**——它需要客户侧的账号与网络，第 1 节给出了拿到之后一条命令就能填上的路径，而不是一句「请相信我们」。
