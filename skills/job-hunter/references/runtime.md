# 生效配置、发送检查与定时恢复

脚本相对于本 Skill 目录；示例 data-dir 是独立测试方案，请替换成用户选定目录。Python 3.10+，无第三方运行依赖。

## 唯一生效来源

- `profile.md` 保存经历事实及更正出处。事实变化只改这里；政策类偏好存入 `policy.json`，不要复制到画像末尾。
- `policy.json` 保存当前执行策略：浏览器选择、城市、薪资、排除项、授权与时段。长对话和日志保留修改原因，不另做一套运行配置。
- `state.json` 保存已经发生的事实、待核对外发、会话与轮次；不反向覆盖用户策略。
- 宿主定时提示只包含 Skill 路径、data-dir、读取当前策略与交付要求。已有旧提示或 automation-guide.md 仅作历史交接；其中与用户最新要求冲突的执行条件以新 policy 为准。首次升级时主动合并已确认的旧规则，不丢弃仍有效的授权。

读取当前配置：
```sh
python scripts/store.py --data-dir ./demo-data effective-config
```

修改前取得运行锁，保留未改字段，准备完整 JSON。用刚读取的 `policyFingerprint` 进行比较后更新；并发变化会拒绝覆盖并要求重新读取。授权来源必须来自用户，不是脚本自行授予。
```sh
python scripts/store.py --data-dir ./demo-data set-policy --token TOKEN --file updated-policy.json --expected-fingerprint FINGERPRINT --evidence "用户在当前对话明确修改的条件"
```

旧配置存 logs/policy-<指纹>.json，仅供恢复与审计。不要把这些个人配置备份打进插件包。

## 可执行排除与一次性授权

这些是可选策略字段，不是所有求职者的默认偏好：
```json
{
  "targets": {
    "excludedCompanies": [
      {"name": "示例公司", "aliases": ["页面上的公司简称"], "reason": "用户明确排除"}
    ]
  },
  "search": {
    "excludeHeadhunterPosted": true,
    "excludedOpportunityGroups": [{"key": "example-project", "aliases": ["项目别称"]}]
  }
}
```

公司规则按名称和明确别名匹配，阻止该公司的全部后续外发；不自动模糊匹配公司集团。猎头和招聘项目规则作用于新沟通、申请及简历发送。已记录的排除身份不能被本次请求的相反描述覆盖，用户更正后先修正对应记录及依据。自然语言 `targets.exclude` 仍由 Agent 阅读，不宣称脚本能理解任意文本规则。

动作填写 `accountLabel`，匹配 policy 中目标平台的账号。平台/对象长期授权可用 `authorization.scope.platforms` 与 `targetKeys` 限定。空的 scope 列表不代表无限授权。

发送前现场观察 `targetFacts`，用于关联职位与招聘方：
```json
{
  "accountLabel": "本人求职账号",
  "targetFacts": {
    "company": "示例用人单位",
    "publisherType": "direct",
    "evidence": "页面显示公司招聘人员身份，已核对完整职位信息",
    "jobKey": "boss:stable-job-id"
  }
}
```

`publisherType` 取 direct / headhunter / unknown。启用猎头排除时，新沟通、申请、简历发送需要本次页面的 direct 证据；未知应先读页面核对，不能凭名称猜测或为了过检查填写 direct。职位已识别的 `opportunityGroup` 写入 jobs 并通过 thread.jobKeys 或 targetFacts.jobKey 关联，跨招聘人重复仍能被排除。

长期 draft / ask 不允许仅凭一段 authorizationEvidence 登记外发。已有用户一次性明确授权时，直接把它记录为 `oneShotAuthorization`，无需重复提问、无需修改长期策略：
```json
{
  "kind": "reply",
  "platform": "boss",
  "targetKey": "boss:stable-thread-id",
  "inboundId": "visible-message-id",
  "evidence": "用户明确要求发送这条回复"
}
```

该对象位于动作的 oneShotAuthorization 字段，kind/platform/targetKey/inboundId 必须与本次动作一致。它可以给一次性目标授权，不能绕过用户公司排除、账号校验或平台限额。人工接管线程的 oneShotHandover 同时需要此明确授权。

`policy.resume.sha256` 约束附件内容；相同文件名但内容变了也会拒绝。其他已授权附件需在一次性授权的 attachments 中写明绝对 path 与 sha256。发送平台已存简历时，可在 answers 填 resumeSha256 与 platformResumeEvidence，后者记录页面文件版本与已授权上传版本的对应依据；脚本不能从页面替你验证文件内容。

## 提交前复核与面试证据

begin 成功只表示登记 pending。真正点击提交前执行：
```sh
python scripts/store.py --data-dir ./demo-data check-action --token TOKEN --id ACTION_ID
```

它重查配置指纹、附件内容、账号、排除项、接管状态、时段和暂停项。失败后不点击；确认还没提交才能 resolve failed。页面是否正确、材料是否真实、授权是否确实来自用户仍需 Agent 判断，检查通过不是平台回执。

线程 stage=interview 必须含 interviewInvite.inboundId/evidence。interviewConfirmed=true 还须有 interview.startAt（带时区）、mode、confirmationEvidence。面试时间可用性与双方是否确实同意由 Agent 按用户策略核对，不能仅为补字段而把询问约面记作邀请。

## 每轮定时记录

`run_ledger.py` 只记录运行，不注册调度器、不操作浏览器。schedule.windows 沿用 search-apply/start、reply-check/start/end/intervalMinutes、daily-report/time。启用时记录 schedule.startDate；catchUpDays 默认7，可设1–30。当天迟到日报和时间窗内最近一个回复轮次会列为待办；旧回复轮次合并，避免醒来后连跑几十次。跨午夜回复窗口拆为两个窗口。

```sh
python scripts/run_ledger.py --data-dir ./demo-data due
python scripts/run_ledger.py --data-dir ./demo-data start --token TOKEN --mode reply-check --date 2026-09-08 --slot 15:00
python scripts/run_ledger.py --data-dir ./demo-data finish --token TOKEN --key run:reply-check:2026-09-08:15:00 --status completed --evidence "已核对变更会话与未读列表"
```

先获取 store 运行锁再 start。恢复旧轮次会返回待核对动作 ID，必须先核对，不能重发 unknown。仍按 policy.search.batchBeforeReply 优先处理未满的投递批次；due 只列待办，不替 Agent 选择执行顺序。日报优先补交，不因投递批次未满而丢失。

日报 finish completed 需要 `--artifact logs/<日期>-daily-summary.md`，文件必须存在于 data-dir。输出报告给用户之后，下一次可记录宿主实际回执：
```sh
python scripts/run_ledger.py --data-dir ./demo-data delivered --token TOKEN --key RUN_KEY --receipt "宿主确认交付的消息或结果标识"
```

completed 表示生成，delivered 表示已交付；不可提前填回执。宿主只能在最终回答后交付时，下一轮先检查上一轮宿主结果并补记 delivered，再判断是否需重新交付。拿不到回执记为交付待核对，不能盲目重复推送。配置 startDate 后，即使上日完全没有触发，也能在 catchUpDays 范围内发现缺失日报；补报只统计该日期的数据并标明缺口。

运行锁不按时间抢占；中断后先核对原执行者已停止及 pending/unknown，再按原 token 释放。doctor 只提示 runBusy，不自动清锁。

## 诊断与升级

```sh
python scripts/doctor.py
python scripts/doctor.py --data-dir ./demo-data
python scripts/doctor.py --compare-skill /absolute/path/to/installed/job-hunter
python scripts/store.py --data-dir ./demo-data report --date 2026-09-08
```

doctor 仅检查本地结构、Python、配置、锁与待核对数量，不输出账户名、正文、文件内容或运行 token。compare-skill 用文件摘要判断副本是否一致。可选 --codex 指定原生可执行文件，只运行 plugin --help 检查 add 能力；不修改安装。

升级保留 v2 状态，无需重新 init 或清空历史。新外发按上述结构记录账号/一次性授权/目标事实；旧 pending 没有配置指纹时只核对已有回执，不直接继续提交。旧线程只在修改面试字段时需要补齐对应证据。
