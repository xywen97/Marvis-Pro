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

## 记忆 Skill、工具与保存状态

内置标准 `skills/long-term-memory/SKILL.md` 已接入主控。保存和查询由主控直接通过记忆工具处理，不调用文件、知识库或编程助手。

- `memory_save {"text":"我的名字是温翔宇，我喜欢榴莲和芒果","wait_seconds":20}`：提交用户明确提供的信息。先持久入队，然后最多等待 20 秒（可设置 0–30 秒）；超时后台继续保存。返回 `request_id`、`status`、`memory_writes` 和错误信息。
- `memory_search {"query":"我是谁","wait_seconds":20}`：等待先前待处理的保存请求，在有限等待后查询 `viking://~/memories`，返回事实内容与来源 URI。仍有请求处理中时返回 `processing`，不能据此断言信息不存在。

主控使用工具前加载 Skill。常见的单独记忆请求（例如“请记住我的名字是…”、“我是谁”、“我喜欢吃什么水果”）由执行器直接加载 Skill 和调用工具，通常只有两次审计工具操作，无需主控模型规划；最终答复根据实际状态生成。复杂或混合请求由主控模型依据 Skill 和工具描述处理。

状态含义：

| 状态 | 含义 |
| --- | --- |
| `saved` | OpenViking 已完成提取，并返回大于零的写入数量，才可确认记忆写入 |
| `processing` | 正在保存，尚未确认完成；后台继续同步 |
| `no_change` | 提取完成但未新增或更新内容，可能已有相同内容或无可提取事实，不能据此确认内容已存在 |
| `unconfirmed` | 提取结束但缺少有效写入数量，不能确认保存 |
| `failed` | 提取失败 |
| `disabled` / `unavailable` | 功能关闭或当前不可访问服务，不执行替代文件操作 |
| `found` / `not_found` | 检索找到事实／本次没有匹配事实，后者不代表整个记忆库为空 |

检索最多注入配置数量的事实摘要，默认 5 条、6000 字符；目录概览与 `.abstract.md` 不当作用户事实。返回的记忆是参考数据，不是操作授权或当前执行证据。

明确保存只导出工具提交的内容。同一轮相同内容按稳定请求 ID 去重；已经明确提交保存的轮次不再自动导出整轮对话，单独记忆查询也不会再次导出已读出的事实。取消等待不会撤销已经提交的明确保存请求。

普通成功自然语言对话仍在完成后自动入队提取。工具原始输出、直接 `/tool` 操作、主动评测、失败和取消任务不自动导出。旧历史不会自动批量导入。

SQLite `memory_outbox` 保留队列与请求状态，已有数据库自动增加请求字段。后台通过稳定远程 session 与任务 ID 恢复，避免响应丢失后的重复导出；处理中每秒轮询提取状态，错误按配置间隔重试。异步任务完成与实际写入数量分别记录，不将提交成功或目录命中作为保存成功。

App 任务中心显示每轮“正在保存／已写入／未新增／失败”等状态，并随后台结果更新；设置页和 TUI F5 显示正在保存的队列数量。完整执行记录保留 Skill 加载、记忆工具结果与 `memory_saved`、`memory_recall`、`memory_error` 事件。检索事件记录实际事实来源 URI。模型在工具仍未确认写入时提前宣称保存完成，最终回复会被执行器纠正并记录 `memory_reply_corrected`。

API `/api/state` 的 `tasks[].memory_requests` 显示每轮请求状态，`memory.service` 显示生命周期状态，`memory.deliveries` 显示队列计数。

删除会话会清理本地待同步记录，已经提取到 OpenViking 的跨会话记忆独立保留。切换分支不会回滚全局长期记忆；如需删除已提取的信息，需单独管理 OpenViking 记忆。

## 外部服务与可选 Docker

设置 `memory.deployment: external`，即可连接已有服务。`.env` 的 `OPENVIKING_URL` 与 `OPENVIKING_API_KEY` 仅用于外部模式；托管服务使用 `auth_mode: api_key`，不会发送 account/user 身份头。外部服务自行管理启动与存储，本地模型模式拒绝非 loopback 记忆端点。

独立 Docker 仍是开发选项：

```bash
.venv/bin/python scripts/setup_openviking.py --docker --start
```

Docker 配置见 `compose.openviking.yaml` 和 `deploy/openviking/ov.conf`，不参与 App/TUI 默认启动。停用记忆：`memory.enabled: false` 并重启引擎。

API 依据：[OpenViking 0.5.0](https://github.com/volcengine/OpenViking/tree/v0.5.0)、[原生服务入口](https://github.com/volcengine/OpenViking/blob/v0.5.0/openviking_cli/server_bootstrap.py)、[会话 API](https://github.com/volcengine/OpenViking/blob/v0.5.0/docs/en/api/05-sessions.md)。
