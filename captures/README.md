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

细节与复现方式见 `docs/LIVE_EVIDENCE.md`。
