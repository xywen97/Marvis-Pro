# OpenViking 原生持久化记忆

Marvis 的 App 与 TUI 自带并自动管理 OpenViking 0.5.0。用户无需安装 Python、Docker 或执行安装命令。SQLite 保存执行树和待同步记录，OpenViking 保存跨会话长期记忆。

## 使用独立发布包

构建产物位于 `dist/native/MarvisXY.app`。将 App 复制到任意目录或 `/Applications` 后双击启动。TUI 入口同样位于 App 内：

```bash
/Applications/MarvisXY.app/Contents/MacOS/marvis --tui
```

构建目录另有 `dist/native/marvis` 终端启动器。两种入口默认共用 `~/Library/Application Support/MarvisXY/` 下的配置、会话和长期记忆，不依赖原项目的位置或 `.venv`。发布包不包含开发者 `.env`、密钥、数据库或历史会话。用户配置首次初始化后保留，替换 App 不会覆盖它。

首次在 App 设置中填写模型端点、模型名称和 API Key，记忆提取复用这个聊天模型。TUI 用 `/model URL MODEL` 设置模型，再用 `/key` 隐藏输入密钥；`/key` 不接受明文参数，也不会生成对话或任务记录。密钥保存在用户数据目录的私有 `.env`，权限为 `0600`，不会通过 API 回显或存入 SQLite。

本地中文嵌入模型 `bge-small-zh-v1.5-f16`（512 维）随包携带，启动时无需下载。记忆提取仍需要已配置的聊天模型；若要完全本地运行，还需要本地生成模型。App 设置与 TUI F5 显示记忆服务状态。

App 设置中的“启用长期记忆”开关立即生效，选择保存在用户数据库中，重启和升级后保留。关闭时停止托管记忆服务、后台同步与检索；已有记忆和待同步记录保留，再次启用后继续使用。任务执行期间需先等待或取消任务，再切换。

## 自动生命周期

发布包的客户端先连接共享引擎；没有引擎时自动启动独立后台引擎。只有引擎拥有 OpenViking 子进程，App 与 TUI 不会各自启动一个数据库。

- OpenViking 绑定随机的 `127.0.0.1` 端口，由引擎生成并使用私有服务密钥。无需用户管理端口。
- 就绪检查包含 `/ready`、认证检查和真实本地嵌入检索；进程存在不能代替实际可用。
- 服务启动在后台进行，界面无需等待。未就绪时本轮使用已有会话上下文，完成的对话保存在待同步队列。
- 异常退出默认最多重启 3 次；失败后显示错误。修改模型配置会重新配置并启动服务。
- App/TUI 客户端通过租约保活。关闭一个客户端不影响其他客户端。最后一个客户端退出且无运行任务、启用的定时工作流时，引擎空闲约 10 秒后退出；客户端崩溃的租约约 20 秒过期。
- 活跃任务和启用的定时工作流保留后台引擎。显式停止引擎会停止其记忆子进程。
- OpenViking 子进程监听来自引擎的管道；引擎异常退出也会关闭管道，避免数据库进程成为孤儿。正常退出使用 SIGTERM，超时后清理进程组。
- `--offline` 不启动记忆服务，也不发送长期记忆请求。

用户数据目录包括：

```text
MarvisXY/
├── config.yaml
├── .env
├── marvis.sqlite3
├── engine.log
└── openviking/
    ├── service.key
    ├── ov.conf
    ├── service.log
    └── data/
```

日志和密钥仅存于本机；服务配置中的模型密钥使用环境变量引用。备份整个用户数据目录即可保留配置、会话和记忆。App 更新使用原持久化目录；升级 OpenViking 存储格式前应备份并验证兼容性。

## 开发环境

源码运行默认仍使用项目 `.marvis/`，方便隔离开发数据。一次性准备本机开发依赖与校验过的模型：

```bash
.venv/bin/python scripts/setup_openviking.py
.venv/bin/python main.py --app
# 或
.venv/bin/python main.py --tui
```

脚本使用构建机的 uv，将 OpenViking 安装到独立 `.venv-openviking`，不修改主项目环境。源码入口的主进程仍直接拥有引擎，关闭拥有者会停止后台；发布包使用独立共享引擎和客户端租约。

## 构建独立 App/TUI

在目标 macOS 架构上构建：

```bash
.venv/bin/python scripts/build_native_app.py
```

构建机需要 uv、Python 3.12+，以及安装原生依赖时需要的构建工具。运行时不需要这些工具。脚本复制完整 Python 运行时，安装 `requirements-bundle.lock` 中锁定的依赖，携带 OpenViking、本地模型、UI 和源码。原生库按构建机架构打包：当前 ARM64 包最低 macOS 14，Intel 包最低 macOS 15，不能跨架构构建后直接声称通用支持。

可复用已解析的专用构建环境：

```bash
uv venv build/native-venv --python .venv/bin/python
uv pip sync --python build/native-venv/bin/python requirements-bundle.lock
.venv/bin/python scripts/build_native_app.py --python build/native-venv/bin/python --resolved-environment
```

构建脚本校验嵌入模型 SHA-256，移位后验证独立解释器和依赖、离线 CLI、共享引擎退出，以及真实 OpenViking 本地嵌入检索。产物含 App、TUI 入口和 ZIP。`scripts/build_local_app.py` 仍提供依赖源码目录的开发启动器。

当前构建生成的是本地可用的独立包，尚未执行 Developer ID 签名和 Apple 公证；公开分发前需要在发布流水线完成这两步。

## 记忆行为与状态

主控在在线自然语言任务开始时检索 `viking://~/memories`，默认最多 5 条摘要、6000 字符。长期记忆是参考数据，不是操作授权或当前执行证据。领域助手由主控按任务需要获得相关记忆。

成功对话的用户输入与最终回答写入独立远程 session，再 commit 提取。原始工具输出、私有助手上下文、直接 `/tool` 操作、主动评测、失败和取消任务不自动导出。旧历史不会自动批量导入。

SQLite `memory_outbox` 保存待同步记录、远程 session 和提取任务 ID。后台重试连接错误，重启继续处理；检查远程消息与已提交任务，防止响应丢失后的重复写入。只有异步提取状态为 `completed` 才标记 `memory_saved`，提取失败单独保留供排查。

API `/api/state` 的 `memory.service` 显示 `starting/ready/restarting/failed/stopped` 等生命周期状态；`memory.deliveries` 显示同步状态计数。任务事件记录 `memory_recall`、`memory_saved`、`memory_error`。

删除会话会清理本地待同步记录，已经提取到 OpenViking 的跨会话记忆独立保留；如需删除它们，需要单独管理 OpenViking 记忆。切换分支不会回滚全局长期记忆，执行树仍只投影所选分支。

## 外部服务与可选 Docker

设置 `memory.deployment: external`，即可连接已有服务。`.env` 的 `OPENVIKING_URL` 与 `OPENVIKING_API_KEY` 仅用于外部模式；托管服务使用 `auth_mode: api_key`，不会发送 account/user 身份头。外部服务自行管理启动与存储，本地模型模式拒绝非 loopback 记忆端点。

独立 Docker 仍是开发选项：

```bash
.venv/bin/python scripts/setup_openviking.py --docker --start
```

Docker 配置见 `compose.openviking.yaml` 和 `deploy/openviking/ov.conf`，不参与 App/TUI 默认启动。停用记忆：`memory.enabled: false` 并重启引擎。

API 依据：[OpenViking 0.5.0](https://github.com/volcengine/OpenViking/tree/v0.5.0)、[原生服务入口](https://github.com/volcengine/OpenViking/blob/v0.5.0/openviking_cli/server_bootstrap.py)、[会话 API](https://github.com/volcengine/OpenViking/blob/v0.5.0/docs/en/api/05-sessions.md)。
