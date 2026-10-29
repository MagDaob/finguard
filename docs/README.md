# 文档索引

当前软件为 0.3.0 模拟业务工作台，提供 Windows 便携版、Web 前端及独立后端。入口见[项目说明](../README.md)。

## 使用与交付

| 文档 | 用途 |
|---|---|
| [Windows 桌面版](windows-desktop.md) | 便携软件启动、数据位置、构建和排障 |
| [发布准备](releasing.md) | 文件布局、许可、版本校验、附件和发布边界 |
| [发布准备记录](release-preparation-validation.md) | 当前许可、附件摘要和实际验证结果 |
| [参赛材料](submission/README.md) | 技术文档、答辩讲稿、源码部署与安全自评 |
| [0.3.0 发布说明](releases/0.3.0.md) | 当前候选的变化、兼容性和限制 |
| [变更记录](../CHANGELOG.md) | 版本状态与交付记录 |
| [API 使用](api.md)、[后端运行](backend.md) | 认证、业务接口、配置和部署 |
| [前端入口](../frontend/README.md) | 前端开发、构建和交互边界 |

## 设计与扩展

| 文档 | 用途 |
|---|---|
| [开发路线](roadmap.md)、[实施计划](implementation-plan.md) | 当前进度与待完成事项 |
| [架构](architecture.md)、[领域模型](domain-model.md)、[设计决策](decisions.md) | 控制边界、对象关系与技术取舍 |
| [前端设计](frontend-design.md) | 页面、状态、确认和证据设计 |
| [前端改版提案](frontend-redesign.md) | LibreChat 结构参考、Geometric Bold 风格及页面设计；已应用前端源码 |
| [服务隔离](service-isolation.md) | 项目与模拟银行的目录、环境和进程边界 |
| [扩展接口](extension-interfaces.md)、[接口准备规划](integration-platform-plan.md) | 银行、模型与插件契约及当前范围 |
| [Agent 设计](deepseek-design.md) | DeepSeek 协议、循环预算和权限边界 |
| [理解与隐私](understanding-and-privacy.md) | 输入完整性、任务追问、验证门槛和脱敏 |

## 验证与来源

| 文档 | 用途 |
|---|---|
| [测试入口](../tests/README.md) | 本机和持续集成检查的运行方法 |
| [桌面验收](windows-desktop-validation.md) | 首次桌面构建的历史记录 |
| [前端验收](frontend-validation.md)、[后端验收](backend-validation.md) | 对应阶段的检查结果与限制 |
| [模型验收](deepseek-validation.md) | 此前真实模型调用与 Agent 对照结果 |
| [项目原文](sources/project-framework.txt) | 用户提供的原始框架 |

历史报告保留当时配置和摘要。当前发布附件的实际结果以其 `VERIFICATION.json` 为准。

[对话式任务入口](conversations.md)说明当前连续对话、任务登记、自动执行、修订与消息恢复范围。
