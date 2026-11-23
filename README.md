# 千川巡航

千川巡航是一个面向 Windows 的本地投放监测与操作复核项目，由本地 Python 服务、Web 控制台和可选 Chrome 扩展组成。源码版默认使用空账户配置。

## 目录结构

| 目录 | 内容 |
| --- | --- |
| `monitor/` | 本地服务、数据模型、规则评估、平台 API 与操作账本 |
| `static/` | 本地 Web 控制台 |
| `extension/` | Chrome 扩展源码 |
| `release/` | Windows 客户端打包、加密载荷与离线授权签发工具 |
| `tests/` | Python 与 JavaScript 测试源码 |
| `tools/` | 控制台启动、只读快照和源码打包辅助工具 |

## 本机运行

需要 Windows 和 Python 3.12 或更高版本。可双击 `启动只读监测.cmd`，或在项目目录执行：

```powershell
python supervise.py
```

源码配置模板 `config.json` 的账户列表为空。平台凭据、运行数据库、日志和授权签发密钥应保存在本机，不要提交到 Git。

## Windows 发布版

授权客户端构建和签发流程见 [`release/README.md`](release/README.md)。离线签发私钥由签发电脑上的 Windows DPAPI 保护，不应放入源码仓库。客户端加密可以提高静态提取成本，但不能阻止控制运行机器的人分析程序。

## 安全与隐私

公开仓库仅包含源码和合成测试数据；本机账户配置、运行数据、截图及历史投放记录不属于此源码快照。更多边界说明见 [`docs/security-and-privacy.md`](docs/security-and-privacy.md)。
