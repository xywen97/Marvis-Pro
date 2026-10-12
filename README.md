# MarvisXY

macOS 个人 AI 工作台与全屏交互式终端。桌面 App、浏览器工作台、TUI 和 CLI 使用同一个本机任务引擎，持久化保存会话、工具执行记录、知识库和定时工作流。

当前版本 **0.2.0** 是可运行的功能实现。具体支持范围与缺口见 [功能对照](docs/FEATURES.md)。

## 截图展示

### 动画办公室

六位管家的工位根据真实执行事件切换待命、规划、工具执行与等待确认状态；点击工位可查看当前任务。

![MarvisXY 动画办公室：管家工位、执行状态与任务统计](docs/screenshots/office.png)

### App 工作台

按 step 展示执行过程，工具结果可折叠；回复支持 Markdown 表格和代码高亮。

![MarvisXY App 工作台：执行步骤、折叠工具结果与代码高亮](docs/screenshots/app.png)

### 执行中的团队

右侧管家头像展示待命呼吸、执行动作与完成反馈；任务标记沿路径在主控和执行管家之间移动，跟随真实工具调用与返回事件。流式更新保留动画节点，不会反复重启动画。

![MarvisXY 运行面板：电脑管家执行任务，主控协调与任务交接](docs/screenshots/team.png)

### TUI 交互式终端

全屏终端展示步骤、说明和工具调用，支持流式文字、命令补全与结果展开。

![MarvisXY TUI：步骤分组、嵌套工具结果与对话回复](docs/screenshots/tui.png)


### TUI 执行步骤树

在同一 session 内选择任意执行节点，返回编辑器后从该位置继续分支。

![MarvisXY TUI 执行步骤树：思考说明、工具调用、返回与最终回答](docs/screenshots/tree.png)

截图使用演示会话；App 图为桌面端共用的工作台界面，TUI 图由实际 curses 终端输出捕获。

## 启动

推荐 macOS 13+，Python 3.9–3.13。当前开发环境为 Apple Silicon / Python 3.9。

```bash
cd /Users/ice/project/marvis-xy
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop.txt -r requirements-office.txt

# macOS 原生窗口（WKWebView，不是浏览器标签页）
.venv/bin/python main.py --app

# 全屏交互式终端
.venv/bin/python main.py --tui

# 不调用模型，验证本地工具
.venv/bin/python main.py --tui --offline

# 浏览器工作台 / 仅启动服务
.venv/bin/python main.py --web
.venv/bin/python main.py --serve

# 单轮 CLI / 逐行交互
.venv/bin/python main.py "检查磁盘空间"
.venv/bin/python main.py --interactive
```

不安装桌面和文档依赖时，TUI、CLI、HTTP 服务、文本搜索、CSV 转换和 SQLite 知识库仍可运行。PyYAML 用于读取集中配置，缺失时使用 `.env` 模型配置。

App 启动后，再启动 TUI 会自动复用同一后台引擎；TUI 关闭不影响 App。所有客户端必须使用相同 `--data-dir`。**启动后台引擎的进程退出时**任务和定时器停止，其余客户端会失去连接。若需要持续运行，可保持 `--serve` 在终端运行。暂未安装 launchd 常驻服务。

### 双击 App

```bash
.venv/bin/python scripts/build_local_app.py
```

生成 `dist/MarvisXY.app`，可在 Finder 双击。此开发版入口指向当前项目及 `.venv`，移动项目后需重新生成；不是可复制到其他电脑的独立分发包。首次安装的 Python/应用可能受到系统自身的 Gatekeeper 或文件访问权限限制。

## 界面

桌面工作台包含：

- **办公室**：六个动画工位，角色状态关联真实任务与工具事件；点击管家查看当前工作，打开对话查看执行过程。显示近期任务范围内的今日完成数、工具调用数与活动任务数，切出页面暂停动画，支持系统减少动态效果设置。
- **工作台**：自然语言对话、历史会话、对话归档/恢复和永久删除、文件搜索、系统检查和整理入口。助手回复与流式内容支持 Markdown 标题、粗体、列表、引用、表格、链接、代码高亮和代码复制；图片可点击放大。
- **知识库**：索引授权目录，按文件名与文本内容搜索，文档读取与图片 OCR 入口。
- **任务中心**：排队、执行、确认、完成、失败与取消状态，实际工具参数和结果。
- **自动化**：创建间隔工作流、手动运行、暂停、恢复、删除；提供磁盘巡检与索引模板。
- **设置**：效率/本地模式，模型端点和名称，文件授权目录。
- **团队状态**：主控与 File / Computer / App / Browser / Search 五类能力的实时执行状态；待命、执行、交接、完成动画，以及待确认、失败与停止反馈。点击执行中的管家打开任务对话，支持系统减少动态效果设置。

采用主模型的结构化工具调用循环，按能力领域分发执行；当前不包含五个独立模型的并行对话上下文。任务按提交顺序执行，防止同一会话或文件操作相互冲突。

六个角色的职责、工具归属与交接规则由 `marvis/team.py` 统一定义。发送“检查团队能力”或 `/tool agent_inspect {}`，可离线查看实际平台、文档解析依赖、OCR 引擎、授权目录与本地模式下禁用的工具。办公室待命详情和团队头像提示展示角色工作原则。近期优化与可复现基准见 [Agent 能力与协作](docs/AGENT_OPTIMIZATION.md)。

## TUI 操作

F1–F5 切换对话、任务、知识库、自动化、设置。Tab 切换页面，↑↓滚动；Ctrl+N 新会话，Ctrl+X 请求停止；Enter 提交，Ctrl+C 或 `/quit` 退出。支持中文字符宽度和输入编辑。输入 `/` 或命令前缀（如 `/ne`）会显示命令联想、参数提示和说明；↑↓选择候选，Tab补全，Esc收起，Enter提交。输入参数时不触发命令名补全；没有候选时 Tab 保留页面切换功能。终端至少 60×15。 TUI 按 step 显示弱化灰色的公开行动说明、生成中的工具调用和嵌套结果。增量文字以约 30 ms 的绘制节拍逐步显示，后台快照按 100 ms 更新，curses 仅提交屏幕差异。工具参数与结果默认折叠；Ctrl+R 进入工具选择，↑↓选择、Enter切换、←折叠、→展开、Esc返回输入；也可用 `/result N` 切换第 N 个工具（省略 N 选择最后一个）。支持鼠标事件的终端可点击工具条目。展开状态按调用 ID 保留，滚动查看历史时暂停自动跟随。

```text
/help
/tree
/result 1
/files "/Users/ice/project/marvis-xy" 搜索关键词
/read "/Users/ice/project/marvis-xy/README.md"
/write "/允许目录/note.md" "需要保存的完整内容"
/organize "/允许目录"
/approve
/reject
/duplicates "/允许目录"
/convert "/允许目录/data.csv" json
/index "/允许目录"
/search 文档关键词
/system disk
/system memory
/system battery
/apps
/open-app "Safari"
/web https://example.com
/web-search 查询关键词
/mode local
/model https://provider.example/v1 model-id
/roots "/工作目录" "/其他允许目录"
/workflow 60 "磁盘巡检" 检查磁盘空间
```

对话页默认实时显示规划状态、模型公开行动说明、工具调用参数、执行耗时与结果；App 通过认证 SSE 实时推送增量。每个 Agent step 显示模型公开行动说明气泡、工具调用和返回结果；说明气泡逐步显示，工具条目使用淡灰色，参数与返回结果嵌在条目下方、默认折叠。工具参数生成期间即可显示调用条目，后续状态与结果更新保留展开状态。界面复用已有 DOM 节点，通过 requestAnimationFrame 平滑显示文本并跟随滚动；向上滚动会暂停跟随。完成后显示正式回复。连接失效时降级为轮询，自动重连。若模型服务忽略流式请求并一次返回全文，执行过程会明确标注本轮未提供分段输出。`/trace` 可以收起或展开过程，F2 保留完整执行记录。对话采用树状缩进，参数和结果归属对应的工具调用；中文长行换行后保留层级。用户消息为青色、工具为蓝色、成功为绿色、错误为红色；处理说明使用弱化灰色（终端不支持文字 alpha 透明度）。显示的是处理说明和真实事件，不展示模型私有推理字段。更新此功能后须退出旧 App / TUI / 后台引擎再启动，避免复用旧引擎。

`/tree`（或 `/t`）打开当前 session 的执行步骤树，包含 user、公开思考/行动说明、tool call、tool result 和最终回答。↑↓选择，←折叠/父节点，→展开，Enter直接跳转，Esc返回。选 user 时退到父节点并将原文放回输入框；选其他步骤时以该步骤作为当前节点、清空输入框，等待下一条指令。新消息在同一 session 内形成分支，原分支完整保留；下一次模型请求只使用选中节点的祖先路径。输入文字进行多关键词搜索，Ctrl+U清空搜索，Ctrl+O切换默认／用户／书签／无工具／全部筛选，大写 L 编辑书签。Ctrl+S 可先生成离开分支的摘要再切换（需要可用模型），Enter 默认不生成摘要。直接输入 `/tree` 或 `/t` 并按回车即可进入选择界面，无需节点 ID；活动任务结束前禁止跳转。旧工具不会自动重跑，已经执行的文件操作不会撤销。旧记录会自动补齐执行节点；首次更新须退出旧引擎并重启。详见 [执行树规则](docs/SESSION_TREE.md)。

完整命令见 `/help`，工作流与任务 ID 可在对应页面查看。包含空格的路径和名称用引号包裹。

App 输入框也支持相同的斜杠命令与短命令，例如 `/help`（`/h`）、`/tree`（`/t`）、`/system disk`（`/sys disk`）。输入 `/` 后显示联想，用 ↑↓ 选择、Tab 补全、Enter 执行。`/tree` 打开当前对话的步骤选择窗口，可搜索并跳转历史；选用户消息会将原文放回编辑器。用户消息、助手回复和公开处理说明气泡下方均有复制原文与“分支到新聊天”按钮；新聊天只继承选中步骤的祖先历史，原对话保留，工具不重跑。执行中的任务结束或停止后才能分支。`/trace` 切换过程显示，`/result N` 切换工具详情；App 的 `/quit` 尝试关闭窗口，不停止共享后台引擎。

普通 CLI 也可使用精确结构化命令：

```bash
.venv/bin/python main.py '/tool file_list {"path":"/Users/ice/project/marvis-xy","query":"README"}'
```



## 模型与运行模式

App 图片支持 Markdown `![说明](https://example.com/image.png)`、PNG/JPEG/GIF/WebP 的 base64 data URL，以及 `![说明](/已授权目录/图片.png)`（相对路径按项目工作目录解析）。本地图片经认证接口读取，沿用目录授权与凭据排除规则，上限 10 MB；本地模式不加载远程图片。渲染库随项目打包，界面不依赖 CDN。Markdown 内的 HTML 经清洗后显示。

沿用 `config.yaml` 的模型与人设配置。密钥只放 `.env`，不会显示在 UI 或存入设置数据库。

```dotenv
OPENAI_BASE_URL=https://your-provider.example/v1
OPENAI_API_KEY=your-key
OPENAI_MODEL=your-model
```

配置优先级：界面保存的端点/模型 > `config.yaml` > `.env` > 进程环境变量。`config.yaml` 自带的端点/模型若不适合你的供应商，请在设置中修改或删去 YAML 对应项。自然语言复杂任务通过 OpenAI 兼容 `/chat/completions` 和 function calling 执行；最多八轮工具调用，失败明确显示，不虚构执行结果。

- **效率模式**：模型可在远程运行，任务和工具结果可能发送给已配置的模型服务。工具动作本身在本机执行。
- **本地模式**：模型固定使用 `http://127.0.0.1:11434/v1` 的 Ollama `qwen3:latest`，不沿用云端 Key，禁用网页读取、网页搜索和打开联网网页；模型重定向被拒绝。需要你提前安装 Ollama 并下载该模型。简单本地工具无需模型。
- `--offline`：禁用模型调用，不等于禁用网络工具；网络工具由本地/效率模式控制。连接已有引擎时沿用其模式。



## 文件与执行保护

默认只授权项目目录。在设置或 `/roots` 中添加具体工作目录。路径解析后检查授权边界，拒绝通过符号链接越界，排除 `.env`、凭据目录与自身数据库。

创建、转换、移动文件和打开应用/浏览器先展示具体计划，再等待用户确认；拒绝和取消不会执行待确认操作。确认五分钟后超时。写入与转换不覆盖现有文件；整理确认后重新检查文件大小、修改时间及目标冲突，移动失败尝试回滚已移动文件。整理目前按类型对一层散落文件归类，不提供删除和通用撤销。

文件检索最多扫描 5000 个文件，返回前 100 个结果；文档读取上限 20 MB / 100000 字符。图片索引仅包含文件名和元数据。OCR 需要系统安装 `tesseract` 与 `chi_sim+eng` 语言包。

`file_list` 支持 `offset`/`limit` 分页，并返回扫描上限与下一页提示；`file_read` 支持字符偏移和分页，文本每页最多 100000 字符，PDF/Word/Excel 仍最多提取 100000 字符。转换文本格式时在 20 MB 内读取完整源文件，避免分页上限导致残缺输出；确认后和写入前复核源文件变化。索引重扫跳过大小与修改时间相同的非空文档，失败提取会重试；知识检索返回命中附近摘要并标记过期索引。OCR 自动选择已安装的中英文语言，只有英文时明确提示。

本机 HTTP API 仅绑定 `127.0.0.1`，要求随机令牌，并检查 Host 和 Origin。访问地址中的令牌及 `.marvis/endpoint.json` 不应分享。`endpoint.json` 以 0600 创建。**不要将此服务反向代理到公网。** 当前未实现远程身份认证、TLS 或移动设备配对。

任务取消是协作式的：正在执行的网络请求或单个工具需要返回后才停止，已经完成的文件变更不会因取消自动撤销。切换设置前需要完成或取消活动任务。重启引擎会将未完成任务标记中断，不重放文件修改；工作流在重启后补一次到期执行，不补跑所有错过的间隔。

## 项目结构

```text
main.py                  App / Web / TUI / CLI 统一入口
marvis/runtime.py        任务队列、确认、模型编排、工作流调度
marvis/capabilities.py   文件、文档、系统、应用、网页、搜索工具
marvis/store.py          SQLite 持久化
marvis/server.py         本机 API 与工作台静态资源
marvis/client.py         复用已有后台引擎的客户端
marvis/desktop.py        macOS WKWebView 宿主
marvis/tui.py            curses 全屏终端
ui/                      无 CDN、无构建依赖的工作台前端
scripts/build_local_app.py  生成本地开发 .app 入口
agent.py, agents/        保留旧版 Agent 原型，当前入口不执行旧版路径
llm_client.py            OpenAI 兼容接口客户端
```

运行数据保存在 `.marvis/`，可通过 `--data-dir` 改为其他目录；敏感文件和数据目录已加入 `.gitignore`。原 `.env` 未被修改。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check ui/app.js
```

测试覆盖路径与符号链接、确认/拒绝/取消、整理过期计划、格式转换、知识检索与持久化、真实 Word/Excel/PDF 解析、模型工具调用、消息顺序、工作流调度、HTTP 认证与双客户端复用。文档测试在未安装可选依赖时跳过。接口测试需要允许绑定本机临时端口。

当前原生窗口已成功启动，TUI 已做中文输入、写入批准和任务记录的实际交互验证。浏览器与原生窗口的自动视觉/点击验收受本轮工具权限限制，尚未完成。

对话列表的 `⋯` 菜单支持归档和删除；点击「已归档」可查看、恢复历史对话。删除前需要确认，清理完整会话树和工具记录；有执行中任务时禁止操作。TUI 使用 `/archives`、`/archive [ID]`、`/restore ID` 和 `/delete ID --confirm` 管理对话。

TUI 所有命令都有明确的短形式，联想与帮助显示括号提示，例如 `/quit (q)`、`/files (f)`、`/workflow-run (wfr)`。输入 `/q` 可直接退出；`/new (new)` 保留原名，`/n` 不执行命令。完整列表见 [TUI 命令与短命令](docs/TUI_COMMANDS.md)。
