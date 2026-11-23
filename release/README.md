# 千川巡航 Windows 发布

项目按职责分为 `monitor/` 服务和投放规则、`static/` 控制台、`extension/` Chrome 扩展、`monitor/ego/` Ego 脚本、`tests/` 验证、`tools/` 维护、`docs/` 文档、`data/` 本机运行数据。发布构建从这些目录按白名单取材。原源码运行方式可继续用于研发，发布客户端使用独立的 Windows 用户数据目录。

## 构建

在 Windows x64 的 Python 3.12 环境安装 `requirements-build.txt`，准备 MSVC C++ 构建工具及 esbuild 0.25.12。运行：

```powershell
python release/build.py --esbuild <esbuild.exe的完整路径> --output <输出目录> --version <版本号>
```

构建输出客户端 ZIP、签发工具 ZIP 和 SHA-256。首次构建把签发私钥写入当前 Windows 用户的 `%LOCALAPPDATA%\QianchuanCruiseIssuer\issuer-key.dpapi`。以后构建使用同一把密钥，现有授权可以跨版本继续使用。密钥留在签发电脑，签发工具可以导出带密码的加密备份以及恢复对应公钥的备份。

新安装的账户表为空。构建白名单不包含生产运行目录、授权凭据、日志、SQLite 台账、旧测试辅助脚本或个人截图。接收方在自己的电脑配置官方 API 和需要使用的浏览器。

## 授权与保护

离线授权使用 Ed25519 签名。签名绑定产品、Windows 机器码、生效签发时间和到期时刻。启动、HTTP 请求、官方 API 请求和 Ego 脚本下发均核对授权。DPAPI 存放最近观察时间，运行中用单调时钟检测明显回拨。重装系统可能改变机器码。离线授权无法在发出后远程撤销，续期用同一机器码重新签发。

业务 Python 模块编译为字节码，控制台、Ego 和 Chrome 扩展一并封入 AES-256-GCM 包。Cython 编译加载器和验证器，PyInstaller 提供 Windows 运行时。服务在内存中加载业务模块，Ego 使用本机一次性令牌读取脚本。浏览器需要执行收到的网页脚本。用户手动导出的 Chrome 扩展包含可供浏览器执行的压缩 JavaScript。

离线程序必须能在本机解密和运行，因此能控制本机执行环境的人仍可能分析运行时内容。授权私钥与客户端解密能力相互独立。

## 验证

在签发机用同一构建环境运行 `python release/smoke.py <客户端ZIP> --owner <签发工具ZIP>`。脚本使用临时数据目录、独立端口及短期测试授权，直接运行两个打包后的程序检查界面初始化，并覆盖加密包、错误或过期授权、机器绑定、时间回拨、续期、工作台 HTTP 入口和后台停止。测试使用空账户配置，结束后清理临时测试数据。
