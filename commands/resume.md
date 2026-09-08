---
description: 将唯一选中的会话交还 Agent 处理，保留原授权范围
argument-hint: "[公司、HR或会话ID] [--data-dir 路径]"
---

# /job-hunter:resume

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 resume 意图。

用户参数：$ARGUMENTS

按 workflows.md 的 resume 执行。匹配唯一线程才更新；歧义时列出必要候选。刷新上下文，保留成功和待核对动作，不把恢复接管当成新的发送授权。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
