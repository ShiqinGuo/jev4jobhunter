---
description: 准备或执行用户已授权的招聘消息回复
argument-hint: "[会话或回复要求] [--dry] [--data-dir 路径]"
---

# /job-hunter:reply

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 reply 意图。

用户参数：$ARGUMENTS

按 workflows.md 的 reply 执行；先读目标会话和关联岗位，再起草或执行具体授权。已有明确发送授权无需再次确认；缺授权则完成草稿。过渡话术同样属于外发。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
