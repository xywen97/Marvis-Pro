# TUI 命令与短命令

完整命令和短命令的参数及行为相同；只接受明确注册的别名，不把任意前缀当作命令。`/new` 等较短名称保留原名，`/n` 不执行任何命令。

联想同时匹配完整名称和短命令，显示 `/quit (q)` 这样的提示；Tab 补全为完整命令，Enter 可以直接执行短命令。

| 完整命令 | 短命令 | 说明 |
| --- | --- | --- |
| `/new` | `/new` | 新建对话 |
| `/tree` | `/t` | 执行步骤树：历史跳转、编辑重发、分支切换 |
| `/sessions` | `/ls` | 列出会话 ID |
| `/archives` | `/las` | 列出已归档会话 |
| `/archive` | `/ar` | 归档指定或当前对话 |
| `/restore` | `/rs` | 恢复已归档对话 |
| `/delete` | `/del` | 永久删除对话及执行记录 |
| `/session` | `/ss` | 切换历史会话 |
| `/files` | `/f` | 文件名和内容搜索 |
| `/read` | `/r` | 读取文档 |
| `/write` | `/w` | 创建文件（需确认） |
| `/organize` | `/org` | 整理目录（需确认） |
| `/duplicates` | `/dup` | 查找重复文件 |
| `/convert` | `/cv` | 转换文档（需确认） |
| `/index` | `/i` | 建立知识库索引 |
| `/search` | `/s` | 搜索知识库 |
| `/ocr` | `/ocr` | 提取图片文字 |
| `/system` | `/sys` | overview/disk/memory/processes/battery |
| `/apps` | `/apps` | 列出 macOS 应用 |
| `/open-app` | `/oa` | 打开应用（需确认） |
| `/web` | `/web` | 读取网页 |
| `/browse` | `/b` | 打开网页（需确认） |
| `/web-search` | `/ws` | 网络搜索 |
| `/mode` | `/mode` | 切换模式 |
| `/model` | `/mdl` | 配置效率模式模型；Key 从 .env 读取 |
| `/roots` | `/rt` | 设置授权目录，空格路径请加引号 |
| `/workflow` | `/wf` | 创建自动化，名称请加引号 |
| `/workflow-run` | `/wfr` | 立即执行工作流 |
| `/workflow-toggle` | `/wft` | 暂停/恢复工作流 |
| `/workflow-delete` | `/wfd` | 删除工作流 |
| `/approve` | `/a` | 批准待确认操作 |
| `/reject` | `/j` | 拒绝操作 |
| `/cancel` | `/c` | 停止任务 |
| `/trace` | `/tr` | 展开/收起对话执行过程 |
| `/result` | `/res` | 展开/折叠第 N 个工具的参数与结果 |
| `/help` | `/h` | 帮助 |
| `/quit` | `/q` | 退出 |

参数格式可在 TUI 中输入 `/help` 或 `/h` 查看。删除对话仍需明确提供 `ID --confirm`，例如 `/del SESSION_ID --confirm`。

鼠标滚轮和触控板滑动可上下查看对话历史、帮助与其他页面，每次滚动移动三行。在历史位置阅读时，新输出不会把视图拉回底部；滚回底部后恢复跟随。在 `/tree` 中也可以滚动选择步骤，再按 Enter 切换。需要终端支持鼠标报告，TUI 启动时自动启用。兼容 macOS 自带 curses 的滚轮事件表示，参见 [ncurses 鼠标接口说明](https://www.invisible-island.net/ncurses/man/curs_mouse.3x.html)。
