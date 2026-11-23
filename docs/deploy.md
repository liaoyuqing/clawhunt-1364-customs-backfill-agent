# 部署文档

## 1. 环境要求

| 项 | 主环境（模块一/二/三） | 模块四虚拟机 |
| --- | --- | --- |
| Python | 3.11+ | 3.11+ |
| 内存 | 2 GB 起 | 16 GB 起（本地 7B 级模型） |
| 出网 | 需访问领星与钉钉开放平台 | **只允许内网**，白名单仅私有 CIDR |
| 磁盘 | 1 GB | 500 GB（发票与支付原件按批次存） |

## 2. 主环境部署

```bash
git clone <repo> clawhunt-1364
cd clawhunt-1364
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest -q
```

### 2.1 配置项

新建 `config/local.yaml`（不入库）：

```yaml
lingxing:
  app_key: <领星应用 app_key>
  app_secret: <领星应用 app_secret>
  base_url: https://openapi.lingxing.com
dingtalk:
  app_key: <钉钉应用 app_key>
  app_secret: <钉钉应用 app_secret>
  sheet_id: <报关批次台账的 sheetId>
  operator_id: <人工确认操作人的 userId>
rules_root: rules
```

环境变量优先于配置文件：`LINGXING_APP_KEY`、`LINGXING_APP_SECRET`、`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`CLAWHUNT_1364_RULES`。

### 2.2 规则包上线流程

1. 改 `rules/*.yaml` 的业务值（例如把 `unit.yaml` 的 `套->个` 从 12 改成 10）；
2. 同时把该文件的 `version` 与 `effective_from` 改到新日期；
3. 跑 `pytest -q` 与 `scripts/sample50.py`，两者都过才允许上线；
4. 上线后旧版本留档在 `rules/archive/<version>/`。

## 3. 模块四独立虚拟机部署

```bash
cd deploy/isolated
docker compose up -d
docker compose exec app python -m prepublish.check --config /etc/clawhunt/isolated.yaml
```

`prepublish.check` 三项自检（任一不过退出码 2）：

1. `egress_whitelist` 每一条都是私有网段（`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`127.0.0.1/32`），出现公网即拒绝；
2. `model_base_url` 指向回环或内网（`127.0.0.1`、`localhost`、`10.`、`192.168.`、`172.16`–`172.31`）；
3. `archive_path` 不在任何云同步目录下（OneDrive / Dropbox / Google Drive / iCloud / 坚果云 / 百度网盘）。

## 4. 回滚

1. 规则包回滚：把 `rules/` 换回上一版目录，改 `effective_from` 为回滚当日，重启进程；
2. 代码回滚：`git revert <commit>` 后重跑 `pytest -q`；
3. 台账回滚：钉钉表格按行版本恢复（每次写入前 `DingTalkSheet.writes` 留了行级写入记录，可据此定位）；
4. 模块四回滚：`docker compose down` 后切回上一镜像 tag，原件目录不受影响。

## 5. 日志

每次批次回填输出一行 JSON：`{"batch_no":..., "rows":..., "issues":..., "pending":..., "elapsed_ms":...}`；写盘位置 `logs/fill-YYYYMMDD.log`。审计行单独写 `logs/audit.log`，字段 `action/operator/digest/at/batch_no`。