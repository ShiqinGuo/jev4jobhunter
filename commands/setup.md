---
description: 初始化或更新画像和策略，保留已有资料与历史
argument-hint: "[简历路径] [--data-dir 路径]"
---

# /job-hunter:setup

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 setup 意图。

用户参数：$ARGUMENTS

读取 profile-schema.md 与 workflows.md 的 setup。使用上下文已有资料，不重复索取；没有浏览器不影响本地初始化。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
