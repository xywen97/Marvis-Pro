# DeepEval 用户主动评测

## 流程与入口

普通任务由主控依据真实执行证据总结，不自动唤起评测，也不以评测作为交付门禁。主控没有用户评测请求时调用 evaluation 会被执行器拒绝。

用户点击右侧评测助手、办公室评测工位的“唤醒评测助手”，或输入 `/evaluate`（别名 `/eval`、`/评测`），先看到信息收集提示。对象与评测要求是必填项，未填不会执行评测。自然语言“唤醒评测助手”也会返回所需信息清单。

1. 用户填写对象类型（代码、输出、指标、任务、其他）与具体内容。
2. 用户填写目标、验收标准、约束；合规评测需给出适用规范。
3. 可选提供参考证据、授权目录中的文件路径、当前对话所选历史路径中的来源任务。
4. 执行器冻结用户请求并调度独立评测助手，DeepEval 原生 G-Eval 评分。
5. 原始需求与评测结果标明“用户主动评测”交回主控，主控汇总要求、结论、指标、证据限制与建议。

有效低分表示未满足全部评测标准，评测任务仍可正常完成；不自动修复、重新采集或重评。用户可修改成果后另行发起。模型错误、依赖缺失或超时表示评测不可用，不产生虚构评分，并将错误和需求交回主控。主控模型不可用时使用执行器保存的需求、分数和理由汇总。最终回复固定包含用户需求与实际评测结果，再展示主控总结。

TUI 或 App 命令也可直接提交 JSON，注意对 JSON 使用单引号保持内部双引号：

```text
/evaluate '{"subject_type":"代码","candidate":"answer = 42","requirements":"结果必须为 42"}'
```

`delivery_evaluate` 参数中 candidate、requirements 必填，subject_type、evidence、paths、source_task_id 可选。用户提供的 evidence 明确标为未经执行器独立验证；不能冒充真实测试。路径按现有目录权限只读采样，来源任务必须属于当前会话的所选祖先路径，只附带可见执行事件，不读取其他助手私有历史。

## 框架与指标

锁定 `deepeval==4.2.8`，使用 LLMTestCase、GEval、DeepEvalBaseLLM，不用本地仿制评分器替代。三个指标为要求符合度、证据一致性、结果适用性，默认阈值均为 0.8。模型判断不替代真实测试，也不能保证全面合规；未提供适用规范或证据时须明确限制。

BridgeJudge 将 SDK 生成的请求与 Pydantic schema 经 NDJSON 交给主引擎，主引擎调用当前配置模型，SDK 校验 JSON 并计算分数。缺少 score 或 JSON/schema 错误时反馈原响应和校验错误，每个指标最多纠正两次，三项最多 9 次模型调用；正常为 3 次。总时间默认 120 秒、单个模型请求最长 30 秒，重试不重置总时限。无效评分、非法工具调用、取消、超时及产物变化均不能被视为有效通过。

请求和结果绑定需求、对象及证据指纹。评测完成前重新读取实际文件快照，变化会使结果失效。最多 20 个产物文件、单文件读取最多 2 MB，总证据上限 60000 字符；省略、截断和大文件均显式标记，不等于全部内容已验证。用户评测不修改文件，不直接调度其他助手。

## 配置与审计

Agent 与 DeepEval 共用项目 Python 3.12+ `.venv`，评测在独立子进程和临时目录中运行，关闭遥测、dotenv 自动读取和版本查询，不登录或自动上传 Confident AI。模型服务凭据只由主引擎使用，本地模式仅允许 loopback 模型。

```yaml
evaluation:
  enabled: true  # 用户评测功能，普通任务不依赖此开关
  threshold: 0.8
  timeout: 120
  # python: /absolute/path/to/python3.12-with-deepeval
```

旧 max_repairs 已不再使用。加载新代码需要重启 App/服务。完整请求保存在 user_evaluation_request，成功评分保存在 user_evaluation_result，需求和协议报告交接保存在 evaluation_handoff，主控汇总保存在 user_evaluation_summary。普通任务不产生 delivery_accepted 或自动交付评分事件。

失败后的普通任务汇总仍按字段压缩到 18000 字符，保留助手状态、摘要首尾、关键工具错误和路径、截断标记及省略数量，避免整体证据被替换成截断通知。当前只接入基于对象及有限证据的 G-Eval，未接入完整轨迹评测、批量数据集、Synthesizer 或 ConversationSimulator。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_evaluation.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node tests/test_evaluation_ui.cjs
```

专项测试运行真实 DeepEval 子进程与指标，裁判响应使用确定性样本，验证普通任务不评测、用户授权、信息收集、原需求交接、低分返回、格式纠正、证据隔离、产物变化、不可用及超时，不证明线上模型评分的绝对可靠性。浏览器测试覆盖右侧入口、命令、办公室入口及主控汇总。
