---
description: 推进一轮求职任务，按已有授权准备或执行；--dry 全程不外发
argument-hint: "[--dry] [--limit 数量] [--data-dir 路径]"
---

# /job-hunter:daily

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 daily 意图。

用户参数：$ARGUMENTS

按 workflows.md 的 daily 推进。--dry 传播到搜索、聊天、过渡回复和所有提交分支，本地可保存候选及草稿。不要创建或重建定时任务。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
