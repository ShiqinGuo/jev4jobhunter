# 画像与求职方案

## 数据目录与事实来源

每个方案一个目录：本次指定的 data-dir → JOB_HUNTER_HOME → ~/.job-hunter。路径可在任意系统使用，不把个人数据写进插件包。一个目录对应一位求职者和一组平台账号；换账号先核对身份，不跨人复用状态。

不同目录的去重、配额和运行锁相互独立。同一平台账号需要共享联系历史和额度时复用同一目录，通过调整求职条件切换方向，不并发启用多个独立目录操作同一账号。

| 文件 | 用途 |
|---|---|
| profile.md | 经历事实、来源、已知限制和沟通偏好 |
| policy.json | 可机器检查的求职条件、额度与已授权范围 |
| opener.md | 可选的固定招呼语；没有就按岗位准备短草稿 |
| state.json | 候选、会话、外发记录、平台暂停状态 |
| drafts/ | 带目标、来源和授权状态的待执行材料 |
| alerts.md | 需要用户决定的事项；相同事件更新状态，不反复追加 |
| logs/ | 候选清单、运行报告和外发记录 |

读取 PDF / Word 等材料时使用当前宿主已有的解析工具或相应 Skill；没有时说明读取限制，不假定可解析旧 .doc 格式。不为了 setup 安装工具或打开浏览器。求职条件或当前回复需要的事实缺失时，按 [intake.md](intake.md) 合并询问本轮关键缺项，其他字段可以保留未知。

profile.md 保存材料来源、工作 / 项目 / 教育经历、技能及证据、待核实事实。执行偏好统一存 policy.json，避免同一规则在画像、指南和定时提示中重复。用户更正事实时保留出处，但正文只维护当前有效值，不让旧值与新值并列造成误用。姓名、联系方式等非必要信息可不收集。没有日期依据不自行推算“几年经验”，没有业绩数据不补百分比。

## policy.json

运行 store.py init 生成默认结构。已有方案用 [runtime.md](runtime.md) 的 effective-config / set-policy 流程更新，防止覆盖新修改。以下是一个**示例方案**，不是适合所有人的默认岗位、城市或授权：
```json
{
  "version": 2,
  "platforms": [
    {"id": "boss", "url": "https://www.zhipin.com", "accountLabel": "本人求职账号"},
    {"id": "company-careers", "url": "https://careers.example.com", "accountLabel": ""}
  ],
  "targets": {
    "keywords": ["Python 后端"],
    "locations": ["上海"],
    "workModes": ["hybrid", "remote"],
    "salary": {"min": 25000, "currency": "CNY", "period": "month", "basis": "base", "tax": "gross"},
    "mustHave": [],
    "preferences": ["业务研发"],
    "exclude": []
  },
  "authorization": {
    "greet": "draft",
    "application": "draft",
    "reply": "draft",
    "share_resume": "ask",
    "commitment": "ask",
    "evidence": ""
  },
  "dailyLimits": {"greet": 10, "application": 10, "reply": 20, "share_resume": 5, "commitment": 5},
  "timezone": "local",
  "activeHours": null,
  "weekdays": [1, 2, 3, 4, 5, 6, 7],
  "recheckAfterDays": 7,
  "allowedLinks": [],
  "schedule": {"enabled": false, "windows": []}
}
```

- platforms 是用户希望访问的站点；可从当前请求添加实际目标，不默认所有人都使用 Boss。示例 URL 需换成用户实际目标。
- authorization 的 draft = 先起草；ask = 完成材料后询问缺少的授权；allow = 用户明确允许此动作在记录的范围内自动执行。当前一次性指令用动作 oneShotAuthorization 记录，无须先改成长期 allow，也不重复询问已有授权；字段与执行校验见 [runtime.md](runtime.md)。
- 默认额度是可调整的本地上限，不是推荐投递目标或平台实际配额。0 表示禁用对应外发；null 表示不设本地数量上限，按平台现场提示和用户授权执行，仍统计每次动作、去重并保留未知结果。用户要求“投到平台当日限额”时显式设为 null，不用一个很大的数字伪装无限。所有动作种类的字段仍须填写，缺失字段属于配置错误。无 warm-up 或固定上班时段的隐含限制。
- timezone 支持 local、明确 UTC 偏移（如 +08:00）或运行环境可识别的 IANA 名称。IANA 数据不可用会明确报错，不静默按 UTC 算额度；可用 local 或确认后的偏移。跨夏令时的定时计划优先使用宿主的时区机制。
- activeHours 可设为 ["09:00", "19:00"]；起点包含、终点不包含，也支持跨午夜。weekdays 为 ISO 周一 1 到周日 7，跨午夜时按当前当地日期判断。只限制外发，读取和本地草稿不受限。
- allowedLinks 是允许对外分享的**完整链接**，如已获授权的作品集。填写它不代表额外授权发送消息。
- schedule.enabled 只是配置意图，不能证明调度器已创建。真实任务 ID 与回执存 state.scheduler；日常运行不会自动重建任务。

## opener 与草稿

opener.md 可包含用户认可的固定文本；招聘方语言或岗位方向不适合时另起草合适版本，不机械套用。平台会自动发送固定招呼语时，先核对实际设置，不默认替用户同步或开启开关。

草稿文件名用本地生成的稳定 ID，避免 HR / 公司名中的路径字符。内容包含：平台、账号、目标 key 与链接、关联 JD / 消息、材料来源、完整文本 / 表单答案、附件绝对路径与 hash、待答问题、授权来源 / 待授权状态、生成时间。发送前重新核对上下文和附件 hash；草稿过期或被用户编辑后以新版本为准。

## 旧版本迁移

- profile.md、opener.md、policy.md 与历史日志原样保留。重复 setup 只补缺失文件、合并用户要求的变更，不让用户在“覆盖或取消”中二选一。
- 先用 store.py migrate 备份并迁移 version 1 的状态。它不会解析自然语言策略，也不会注册旧 cron。
- 旧 policy.md 存在时 init 只生成 policy.example.json，不用新默认值覆盖旧策略。Agent 阅读旧文件，向 policy.json 转写条件：platform → platforms；cities / titleKeywords → targets；minSalary 的 K/月乘以 1000 后显式记 CNY/month；dailyCap → greet 日上限。保留原来用户明确规定的时段、warm-up、排除条件与通知偏好，额外说明可以写 profile / policy 的扩展字段，由 Agent 执行。
- conservative 对应草稿；balanced 与 aggressive 的实际差别在旧实现中不清晰。根据现有会话或策略记录中的明确授权还原具体动作范围，不能把档位名扩展成发送附件、确认时间等新授权。无法确认的部分保持待授权，其他已知工作继续。
- 旧筛选 skipped 可重新评估；旧 chatted / replied / interview / closed 等保留 legacyContacted 标记，避免历史数据迁移或归档导致重投。旧未知状态也先保留联系标记，核对之后再处理。
- 旧 dayKillSwitch 转成待复核暂停，不因为日期改变就解除；旧 runLock 存在时先确认旧运行结束，备份后清除旧锁再迁移。保留旧审计用于接管核对。
