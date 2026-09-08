---
description: 只读核验当前 Agent 浏览器能力、平台登录和页面流程
argument-hint: "[平台或URL] [--data-dir 路径]"
---

# /job-hunter:recon

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 recon 意图。

用户参数：$ARGUMENTS

按 drivers.md 选择当前宿主浏览器，输出 available / unavailable / unverified。可填写搜索框，不填写消息或申请表，不点击立即沟通、发送或提交。无会话、无搜索结果不能直接判为故障。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
