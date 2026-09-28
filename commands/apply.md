---
description: 对用户选中的职位准备或执行申请，核对材料与回执
argument-hint: "[职位URL、候选ID或草稿ID] [--dry] [--data-dir 路径]"
---

# /job-hunter:apply

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 apply 意图。

用户参数：$ARGUMENTS

按 workflows.md 的 apply 执行。明确目标和材料后执行已授权的申请；--dry 只准备本地答案及附件清单。提交前登记 pending，结果不明确记 unknown，不能自动重投。
