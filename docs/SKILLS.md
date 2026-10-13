# 标准 Agent Skills

MarvisXY 支持基于 `SKILL.md` 的 Agent Skills 格式。技能提供任务说明与资源，实际动作仍通过 Marvis 的现有工具、助手职责和审批链路执行。

## 安装与发现

默认按以下顺序发现技能；后面的目录覆盖前面的同名技能，并记录诊断：

1. 应用资源中的 `skills/`，源码模式对应项目 `skills/`。
2. 应用资源中的 `.agents/skills/`，源码模式对应项目 `.agents/skills/`。
3. 当前数据目录中的 `skills/`。独立 App/TUI 默认是 `~/Library/Application Support/MarvisXY/skills/`；源码默认是项目 `.marvis/skills/`。
4. `config.yaml` 中 `skills.paths` 指定的额外技能库目录。相对路径以应用资源根目录为基准；发布包建议使用绝对路径。

每个库目录下直接放技能文件夹。例如：

```text
skills/
└── answer-style/
    ├── SKILL.md
    ├── references/
    │   └── examples.md
    ├── scripts/
    └── assets/
```

也可以显式使用个人技能库：

```yaml
skills:
  paths:
    - "~/.agents/skills"
```

不会默认扫描个人目录。仅安装自己信任的技能。每轮自然语言任务开始时刷新目录，或调用 `skill_list` 主动刷新；配置搜索路径的变更需要重启引擎。

## 文件格式

`SKILL.md` 使用 UTF-8，包含 YAML frontmatter 和 Markdown 正文：

```markdown
---
name: answer-style
description: 在用户要求简洁介绍某个主题时，先列三个要点，再按需解释。
---

先给三个要点。仅依据当前用户输入和已验证的结果作答。
需要格式示例时，读取 references/examples.md。
```

必填字段是 `name` 和 `description`。名称与目录名一致，最长 64 字符，使用小写 ASCII 字母、数字和单个连字符；描述最长 1024 字符。可选字段支持 `license`、`compatibility`、字符串映射 `metadata` 和实验性的 `allowed-tools`。未知字段不会改变运行行为。重复 YAML 字段、非法格式、空正文等会单独报告，不阻止其他有效技能使用。

## 主控如何使用

主控初始提示词只提供技能名称和描述。它依据任务选择技能，用户也可以直接说“使用 answer-style 技能回答”。主控先调用 `skill_load` 取得完整正文，再按需使用 `skill_read` 读取引用的资源；主控向领域助手转交任务时提供相关技能指令。

- `skill_list {}`：列出技能并刷新目录，返回无效技能和覆盖诊断。
- `skill_load {"name":"answer-style"}`：加载完整指令、元数据、路径和 SHA-256。
- `skill_read {"name":"answer-style","resource":"references/examples.md","offset":0,"limit":8000}`：读取本任务已加载技能中的文本资源，返回 `has_more` 与 `next_offset`。

所有加载与读取记录为普通 `tool_start/tool_result/tool_error`，可在 App 任务中心或 TUI F2 查看；`/api/state` 的 `skills` 提供当前目录清单和诊断。App、TUI 和 CLI 共用后台引擎。

离线模式也能通过显式工具操作检查安装情况：

```bash
./dist/native/marvis --offline --data-dir /tmp/marvis-skill-check '/tool skill_list {}'
```

示例指定的数据目录是独立测试环境，不会读取正式用户数据目录中的技能；测试前将技能安装到该目录的 `skills/`。

## 权限与实现范围

Skill 指令不能覆盖用户要求、权限边界或工具所属助手。`allowed-tools` 作为元数据保留，不会自动授权或增加工具。脚本只能读取源码，不会在发现或加载时执行；执行仍由编程助手通过 `code_execute` 处理，遵循目录授权和审批，本地模式仍禁用代码执行。用户目录中的技能脚本通常需要另行授权其工作目录。

资源只允许技能目录内的普通相对路径；拒绝 `..`、隐藏资源和符号链接。当前资源读取支持 UTF-8 文本，不直接渲染二进制图片等 assets。

当前有界读取限制：最多发现 128 个有效技能，单份 `SKILL.md` 最多 64 KiB，正文最多 16000 字符，元数据最多 4000 字符；完整加载结果超过 22000 字符时拒绝加载，不静默截断指令。附属文本资源最多 256 KiB，每页最多 8000 字符。大型技能请把详细说明放到 `references/`。

本次提供格式校验、发现、模型按需加载、资源读取和审计链路；不包含远程市场下载、自动依赖安装、独立技能脚本运行器；内置 long-term-memory Skill 使用专用 memory_save、memory_search 工具，说明见 [长期记忆](OPENVIKING_MEMORY.md)。旧的 Python `BaseSkill` 示例保留，与新的标准 Skill 加载器独立。

格式参考：[Agent Skills 规范](https://agentskills.io/specification)。
