# 模块四独立虚拟机部署

模块四处理发票原件与对公支付凭证，要求数据不出内网、模型本地。全部命令都在买家自己的虚拟机上跑，不依赖交付方服务器。

## 镜像与启动

镜像由本目录的 `Dockerfile` 现场构建（`python:3.11-slim` + `pip install -r requirements.txt`，运行期只依赖标准库与 PyYAML）：

```bash
docker compose build
docker compose up -d
docker compose ps          # 等 STATUS 出现 (healthy)
```

compose 里 `build:` 指向本仓库源码，`command` 与 `healthcheck` 执行的都是同一条 `prepublish.check`：自检不过 → 容器退出或不健康 → 编排层不会放流量进来。

## 启动前自检

```bash
python -m prepublish.check --config deploy/isolated/isolated.example.yaml
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `--config` | 是 | 自检配置 yaml，必须含 `egress_whitelist`（列表）、`model_base_url`、`archive_path` 三个键 |

合规配置（`deploy/isolated/isolated.example.yaml`）跑出来是这样，末行 `ok`、退出码 `0`：

```text
config: deploy/isolated/isolated.example.yaml
egress_whitelist: ["10.20.0.0/16", "192.168.10.0/24", "127.0.0.1/32"]
model_base_url: http://127.0.0.1:11434/v1
archive_path: /srv/invoice-archive
cloud_sync_markers: OneDrive、Dropbox、Google Drive、iCloud、坚果云、百度网盘
ok
```

配置样例：

```yaml
egress_whitelist:
  - 10.20.0.0/16
  - 192.168.10.0/24
  - 127.0.0.1/32
model_base_url: http://127.0.0.1:11434/v1
archive_path: /srv/invoice-archive
```

三项检查（判定函数全部来自 `core/preflight.py`，本包不复制判定逻辑）：

1. **出网白名单只有私有网段** —— 每一条都要 `ipaddress.ip_network(entry).is_private` 为真，出现公网地址即报 `public network in egress whitelist: <cidr>`；空列表报 `egress whitelist is empty`；
2. **模型地址是本地** —— 主机名必须是 `127.0.0.1` / `localhost` / `10.*` / `192.168.*` / `172.16`–`172.31`（`172.32` 以上属于公网，按 `core/preflight.py` 的 RFC1918-B 规则拒绝），否则报 `model base_url is not a local address: <url>`；
3. **原件目录不在云同步路径** —— 绝对路径里出现 OneDrive / Dropbox / Google Drive / iCloud / 坚果云 / 百度网盘 任一即报 `archive path is inside a cloud sync folder`。

## 退出码语义

| 退出码 | 含义 | 输出 | 处置 |
| --- | --- | --- | --- |
| `0` | 三项全过，允许启动 | stdout 末行 `ok` | 正常启动模块四 |
| `2` | 至少一项不过 | stdout 打印 `problems: N` 并逐条 `[i] 原因` | **阻断启动**，逐条修配置后复跑 |
| `1` | 配置文件缺失、不是合法 YAML、顶层不是映射或缺必需键 | stderr `配置加载失败：...` | 修配置文件路径或内容 |

`docker-compose.yml` 把该命令设为容器启动命令；退出码 `2` 时容器直接退出，模块四进程不会起来（`restart` 策略也不该设成 `always`，否则会反复重启）。

改坏配置时的实际输出（三条问题一次全列，不打一条就停），退出码 `2`。下面按 Linux 虚机跑的样子抄；Windows 上 `archive_path` 会经 `os.path.abspath` 规范化后再打印，路径前缀按本机盘符显示：

```text
problems: 3
  [1] public network in egress whitelist: 8.8.8.8/32
  [2] model base_url is not a local address: https://api.example.com/v1
  [3] archive path is inside a cloud sync folder (OneDrive): /Users/bob/OneDrive/invoice-archive
```

## 反向断言

测试已覆盖三个方向，且三者同时错时三条问题一次性全列：

| 文件 | 验什么 |
| --- | --- |
| `tests/test_ac4_isolation.py` | 把公网 IP 加进白名单、把模型地址改成 `https://api.openai.com/v1`、把原件目录放到 `OneDrive` 下，三种都必须失败，三者同错时 `problems` 恰好 3 条 |
| `tests/test_prepublish_entrypoint.py` | 用 `subprocess` 真跑 `python -m prepublish.check`：合规配置退出码 0 且末行 `ok`；含公网白名单 + 公网模型地址 + 云同步目录的配置退出码 2 且 `problems` 条数等于 3；`172.40.0.5` 按公网拒绝；配置缺失退出码 1 |

复现：

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_ac4_isolation.py tests/test_prepublish_entrypoint.py
.venv/Scripts/python.exe -m prepublish.check --config deploy/isolated/isolated.example.yaml; echo "exit=$?"
```

## 上线清单

逐条对应 AC4，细节见 `docs/operations.md` 第四节「模块四单独虚拟机上线清单」：单独虚拟机、模型本地、白名单只留内网 CIDR、原件目录不在云同步路径、启动前自检退出码 2 即阻断。
