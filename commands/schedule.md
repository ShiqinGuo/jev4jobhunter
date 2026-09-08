---
description: 使用当前宿主调度器创建、更新、查看或取消求职计划
argument-hint: "[时间与频率 | 查看 | 暂停 | 取消] [--data-dir 路径]"
---

# /job-hunter:schedule

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 schedule 意图。

用户参数：$ARGUMENTS

按 workflows.md 的 schedule 执行。使用真实可用的调度工具和回执；确认实际时区与运行条件；按任务 ID 和数据目录更新，避免重复任务。定时不增加外发授权。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
