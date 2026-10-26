# 源码组织

当前使用 `finguard` Python 包。所有模块均可通过项目环境导入。

| 已有位置 | 职责 |
|---|---|
| [finguard/domain/values.py](finguard/domain/values.py) | 整数分金额、币种、引用编号、资源归属和基础模型 |
| [finguard/domain/models.py](finguard/domain/models.py) | 五个核心对象、转账参数、计划依赖和对象关联 |
| [finguard/domain/states.py](finguard/domain/states.py) | 任务和操作的独立状态图与迁移守卫 |
| [finguard/domain/errors.py](finguard/domain/errors.py) | 可分类的领域错误 |
| [finguard/tools/registry.py](finguard/tools/registry.py) | 工具元数据、能力约束和输入输出校验 |
| [finguard/tools/transfer.py](finguard/tools/transfer.py) | 转账提交与状态查询契约 |
| [finguard/scenarios.py](finguard/scenarios.py) | 虚构转账场景加载与一致性检查 |
| [finguard/cli.py](finguard/cli.py) | 场景校验命令与配置读取 |

认知、控制、执行、适配器、存储和评估模块按[实施步骤](../docs/implementation-plan.md)逐步增加。当前工具契约没有业务调用入口。
