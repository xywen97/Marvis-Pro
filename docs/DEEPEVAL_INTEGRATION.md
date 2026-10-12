# DeepEval 交付门禁

## 流程

主控与领域助手完成工作后生成候选答案 → 执行器调度独立 `evaluation` 助手 → DeepEval 原生 `GEval.measure()` 计算指标 → 三项达标才保存最终答案。评测不通过时，指标理由反馈主控，主控按需重新调度原助手修复实际成果和验证，再提交新答案。默认最多修正两次；主控轮次、助手调度及全任务模型/工具调用暂不设次数上限，助手不能递归转接。

评测助手的任务和结果与其他助手使用相同 version 1 schema。它使用独立裁判模型上下文，只接收任务要求、候选交付、执行器证据和产物快照，不继承其他助手的私有历史。其 `delivery_evaluate` 工具无写入权限，不能调用 `agent_dispatch`。专属策略来自 `marvis/team.py`，具体裁判约束由 `marvis/evaluation.py` 构造；评分标准和计算由 `marvis/evaluation_worker.py` 中的 DeepEval 实现。

## 真实框架与模型适配

依赖锁定 `deepeval==4.2.8`。采用 SDK 的 `LLMTestCase`、`GEval`、`DeepEvalBaseLLM`；没有本地仿制评分器，也没有用关键词或自填分数替代 DeepEval。

`BridgeJudge` 是 SDK 的自定义模型适配器：DeepEval 生成评测请求与 Pydantic schema，经 NDJSON 发送给主引擎；主引擎调用当前运行模式的 `LLMClient`，SDK 再校验模型 JSON、计算分数和通过状态。评测助手不持有云 API Key，只有主引擎连接已配置模型。本地模式仍只允许 loopback 本地模型。

指标为任务完成度、证据一致性、交付质量，默认阈值 0.8，必须全部通过。提供明确的 evaluation_steps，避免额外生成评分步骤；不要求人工臆造 expected_output。每次最多 6 次裁判调用、总评测时间默认 120 秒，每个模型请求最多 30 秒；评测调用计入主任务审计统计。无效 JSON、无效分数、进程错误、取消、超时、缺少模型和依赖都会停止交付。框架指标具备模型判断的不确定性，代码任务仍须真实测试/构建，不能用评分代替验证。

官方依据：[G-Eval](https://deepeval.com/docs/metrics-llm-evals)、[自定义评测模型](https://deepeval.com/guides/guides-using-custom-llms)、[源码](https://github.com/confident-ai/deepeval)。

## 安装和配置

主引擎已升级为 Python 3.12.14。Agent 和 DeepEval 共用项目 `.venv`，最低要求 Python 3.12。评测依然在独立子进程及临时目录中运行，统一 Python 环境不合并助手上下文，也不改变通信协议。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop.txt -r requirements-office.txt
```

`config.yaml`：

```yaml
evaluation:
  enabled: true
  threshold: 0.8
  max_repairs: 2
  timeout: 120
  # python: /absolute/path/to/evaluation/python
```

默认使用主引擎当前解释器（`sys.executable`），即项目 `.venv/bin/python`；不再自动查找 `.venv-eval`。未安装时返回明确失败，不悄悄采用其他评分方式。运行中的 App、服务或 TUI 需要重启以加载新代码和配置。当前开发版 macOS App 指向本项目目录，能使用同一评测环境。

默认所有主控自然语言候选答案均经过门禁；直接创建/修改/转换文件及代码命令结果也评测，低层只读查询、应用启动和网页打开等确定性操作不强制语义评测。直接工具操作评测失败时停止交付，可将评测问题交给主控自然语言任务继续修复。已执行且确认的文件操作不会自动回滚；不通过代表未获得质量放行，而非文件不存在。离线模式没有裁判模型时，相关交付同样不能获得放行。只有用户显式关闭 `evaluation.enabled` 才跳过，并记录 `evaluation_skipped`。

## 证据、审计和边界

任务和工具证据由执行器提供，不接受模型伪造的 evidence 或 passed 参数。产物从工具返回及实际文件操作路径发现，再通过目录权限读取；最多 20 个文件、单文件 2 MB，每个快照最多 12000 字符。超过整体证据 60000 字符时拒绝评测，要求缩小任务。大文件和截断显式标记，评测不等于检查全部二进制内容。证据包含失败、拒绝和实际测试输出，标准要求参考修正后的最新证据，而非因旧错误永久阻止任务。

评测结果绑定候选答案 SHA256 和执行证据/产物指纹；修改答案或产物后不能复用旧放行。同一次任务的完全相同输入可复用原结果，避免重复费用。SDK 的独立子进程运行在临时目录，禁用遥测、dotenv 自动加载和版本查询，不登录或自动上传 Confident AI；环境仅继承运行必需变量，文件和模型凭据不传给子进程。

候选答案以 `delivery_candidate` 保存在审计中，不作为用户最终回复提前展示。工具行动说明仍可展示；最终回答只有通过后保存。裁判理由与分数保存在任务事件、统一助手报告和工具结果里，可在执行记录查看。`delivery_accepted` 是执行器产生的放行事件，助手说“已通过”不能替代它。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_evaluation.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

评测专项测试运行真实 DeepEval 子进程和指标，使用确定性裁判回复以验证控制流程；这验证 SDK 接入和门禁，不证明具体线上模型的评分可靠性。旧功能回归测试显式关闭评测，以隔离原功能；线上默认配置和评测专项测试均开启门禁。部署后应结合实际交付样本调整阈值和评分标准。


2026-10-12 环境升级：原 Python 3.9 主环境及独立评测环境已保留在忽略版本管理的 `.cache/venv-backups/`，不再用于启动。重建环境时不要将虚拟环境简单改名作为新的运行环境，脚本 shebang 可能仍指向原路径；应在目标 `.venv` 路径重新创建并安装依赖。

## 失败后的汇总与证据范围

任务整体失败不会把此前成功的工具步骤标为失败。执行块按自身返回值显示完成、存在错误或任务中断；工具返回结构中的 error 和失败状态也会显示真实错误。

评测的 20 个文件上限只统计本任务成功创建、编辑或转换的文件，目录遍历和只读检查不计为交付产物。只读文件另作为参考来源，最多附带 8 份有限长度快照；每条工具结果保留摘要、截断标记及完整结果的哈希，完整结果仍在执行日志中。评测不能把截断内容视为已验证。

评测不可用或持续不通过时，主控仍输出失败后的状态汇总，说明已完成、失败及未验证的工作；汇总不重新提交候选成果，也不代表质量放行。优先生成主控说明，模型不可用或说明生成失败时使用执行器保存的助手报告。任务保持 failed，只有成功评测才产生 delivery_accepted。

## 当前利用范围与待接入能力

当前交付门禁正确调用了 DeepEval 的 LLMTestCase、GEval、DeepEvalBaseLLM 和 schema 校验，并将结果反馈给主控。三个指标均为自定义 G-Eval，“任务完成度”不是框架的 TaskCompletionMetric。当前是答案层交付评分，尚未接入原生 observe/tracing、组件及轨迹指标、轨迹自动构建测试用例、EvaluationDataset 批量评测、Synthesizer 或 ConversationSimulator；不能把它描述为完整 Agent 自动化评测。项目中也没有 RunRecord、ChecksMetric 或固定 followup_answer 模拟器，那些是其他接法的示例，不是本项目现状。

执行器审计、文件哈希和产物快照仍用于权限、界面和交付一致性，不应因接入 tracing 而删除。当前把证据投影成有限长度文本，字段截断可能导致裁判无法核实路径、总数或工具状态；原生 tracing 接入后，应让轨迹与组件指标使用保留结构字段的证据，并保留独立文件快照及真实测试结果。该缺口不应通过调低评分阈值掩盖。

后续应优先接入原生 tracing 与 TaskCompletionMetric 等轨迹指标；Synthesizer 与 ConversationSimulator 属于离线回归/能力评测流程，按需生成样本和模拟交互，不需要在每次用户交付时重新出题。编程任务仍需项目环境、真实测试和验收断言。memory 管理负责上下文及长期记忆，不承担运行轨迹评分或循环防护。

助手调度没有总次数上限，旧配置 max_dispatches 不再生效。旧配置 max_rounds、max_model_calls、max_tool_calls 也不再生效。相同请求重复转接防护、助手独立轮次、评测修正次数及工具/评测超时仍各自生效；模型和工具次数仅用于审计统计。
