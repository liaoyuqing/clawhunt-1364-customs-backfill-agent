# 模块四独立虚拟机部署

模块四处理发票原件与对公支付凭证，要求数据不出内网、模型本地。

## 启动前自检

```bash
python -m prepublish.check --config /etc/clawhunt/isolated.yaml
```

配置样例（`deploy/isolated/isolated.example.yaml`）：

```yaml
egress_whitelist:
  - 10.20.0.0/16
  - 192.168.10.0/24
  - 127.0.0.1/32
model_base_url: http://127.0.0.1:11434/v1
archive_path: /srv/invoice-archive
```

三项检查：出网白名单只有私有网段、模型地址是本地、原件目录不在云同步路径。任一不过退出码 2，进程不启动。

## 反向断言

测试 `tests/test_ac4_isolation.py` 已覆盖：把公网 IP 加进白名单、把模型地址改成 `https://api.openai.com/v1`、把原件目录放到 `OneDrive` 下，三种都必须失败，且三者同时错时三条问题一次性全列。