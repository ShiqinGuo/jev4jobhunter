# 运行入口与参数

Android CLI 见 [android.md](android.md)，自动管理运行锁。以下网页入口由调用方获取锁，并在结束时按 token 释放；只读 `status`、`resume-context` 和报告不需要锁。脚本路径相对 Skill 根目录，Python 3.10+；网页与本地状态仅依赖标准库。

## 当前配置

`profile.md` 保存事实，`policy.json` 保存策略，`state.json` 保存执行记录。字段与动作 JSON 见 [state-schema.md](state-schema.md)，画像初始化见 [profile-schema.md](profile-schema.md)。

```sh
python scripts/store.py --data-dir DATA effective-config
python scripts/store.py --data-dir DATA lock acquire
python scripts/store.py --data-dir DATA set-policy --token TOKEN --file updated-policy.json --expected-fingerprint FINGERPRINT --evidence "用户本次更正"
```

更新提交完整 JSON 并保留未改字段；指纹变化时重新读取。旧配置备份到 logs/policy-<指纹>.json。profileFingerprint 是本地文件字节摘要，不是在线简历版本；用户修改在线资料后按实际页面和用户事实同步本地。

策略或画像变化时，先暂挂未发送的旧候选、核对已经开始的动作，再 start-query 重新筛选。保留成功、pending、unknown 与联系历史。升级保留 v2 状态；旧动作缺少审核指纹时只核对原回执，不补当前指纹冒充原审核。

## 网页入口

固定 Kimi session 的建立与恢复见 [drivers.md](drivers.md)，平台限制见 [browsing-safety.md](browsing-safety.md)。

```sh
python scripts/browser_actions.py --data-dir DATA --operation resume-context
python scripts/browser_actions.py --data-dir DATA --token TOKEN --session SESSION --operation OP --file input.json
```

输入为 UTF-8 JSON。`waitMs` 为 0–10000 的整数，默认 4000；等待在一次 Kimi evaluate 内观察，身份与内容连续稳定才返回 ready。超时保留 pending/unknown。优先业务入口，不重复手动等待、登记或复核。

| 业务操作 | 输入与完成边界 |
|---|---|
| `ensure-page` | `{"page":"chat","waitMs":4000}`；page 为 jobs/chat/resume，复用或正常导航后核对；职位页仍需确认筛选 |
| `open-detail-and-wait` | `{"key":"boss:JOB_ID"}`；打开一次，等精确岗位全文稳定 |
| `open-conversation-and-wait` | `{"recipient":{"name":"HR","company":"Company"}}`；打开唯一会话并等待身份、消息稳定 |
| `reply-and-verify` | `{"request":REVIEWED_CHAT_REQUEST}` 或 `{"actionId":"ACTION_ID"}`；普通回复的登记、填写、复核、单次发送与回执；既有已尝试动作只核对 |
| `wait-chat-receipt` | `{"actionId":"ACTION_ID"}`；被动核对回复或附件回执 |

### 搜索与详情

`start-query` 只保存意图，不操作筛选。例如 `{"city":"杭州","keyword":"Python后端","experience":["应届生","1年以内","1-3年"],"salary":["10-20K"],"accountLabel":"当前显示名"}`。值以当前 policy 和真实选项为准。

`search.queries` 精确约束查询词。experienceFilter / salaryFilter 的 allowedLabels 约束可选项，配置 selectedLabels 时必须完整匹配；未配置才允许其子集。其他策略条件按当前平台控件设置并保存证据。

| 操作 | 输入与前置条件 |
|---|---|
| `control` | 当前观察的控件 ID 与 value；interaction 为 click 时点击，无该字段的菜单用 hover，再观察选项 |
| `capture-list` | 实际账号和筛选已验证，登记自然加载的新卡片批次 |
| `screen-many` | `{"batchId":"BATCH_ID","reviews":[{"key":"boss:JOB_ID","decision":"shortlisted","evidence":"去重和明确排除项检查"}]}`；同批次原子保存；decision 也可 skipped/deferred |
| `collect-details` | `{"keys":["boss:job-a","boss:job-b"],"waitMs":4000}`；串行收齐当前批次 shortlisted 的全文，已有活动详情直接纳入；返回详情组 |
| `evaluate-details` | `{}` 或 `{"model":"jev-1.13.0"}`；整组一次 Jev，自动保存决定，见 [jev.md](jev.md) |
| `review-detail-group` | 宿主代评时用 `{"groupId":"GROUP_ID","reviews":[{"key":"boss:JOB_ID","decision":"apply","evidence":"JD 判断依据"}]}` 覆盖全部已读项；不发送 |
| `submit-reviewed-detail` | 原 submit 的 request；重新定位、比较完整 JD、复用决定并发送一次。文本变化返回 review-required，再 review-detail/submit |
| `dismiss-receipt` | 成功后关闭当前确认层，再处理下一项 |
| `scroll` | 当前批次与活动详情、外发均收尾后滚动一次，再 capture-list 核对新增 ID |
| `finish-list-read` | 已处理到可见末尾且没有新增/加载状态，传 evidence 结束此次读取；不声称结果穷尽 |

恢复或处理单项时保留 `screen`（key、decision、evidence）、`open-detail`（key）、`review-detail`（key、apply/skipped/deferred、evidence）。`defer-detail` 只暂挂中断的详情打开，须先取得本轮新 inspect；不用于取消已发送动作。`revisit-candidate` 接收 key/evidence，把自然可见的 deferred 或遗留候选重新加入批次；成功/unknown 不复审重发。`restore-filters` 恢复查询，列表变化时保留 retainedBatches/backlog。

### 招呼与回执

`submit` 接收 `{"request":ACTION}`，只支持 BOSS 新沟通 `contentMode: "platform-default"`、`content: null`。点击前登记 pending 并保存账号、岗位和页面基线。新匹配成功提示出现即保存，无需连续两次；默认等待 4000ms。传输异常不再次点击。

`reconcile {"actionId":"ID","waitMs":4000}` 只核对原动作。若返回 inspect-chat-and-reconcile-no-resend，先进入聊天核对原岗位、公司、招聘者及本次时间的新消息与送达标志。证据完整后用 store resolve 原 action 为 succeeded，再 reconcile 同步候选；否则保留 unknown。聊天补查后恢复查询。观察到的真实招呼另存 observedContent，未知正文不补占位文本。

### 聊天与附件

| 操作 | 输入与边界 |
|---|---|
| `open-page` / `inspect-chat` | page 为 chat/jobs/resume；观察当前自然会话列表和已打开对话 |
| `open-conversation` / `chat-job-detail` | recipient.name/company 唯一匹配；后者打开“查看职位”，再 inspect-session/select-page 核对实际详情标签 |
| `prepare-chat-send` | `{"request":ACTION}`，kind 为 reply/share_resume；绑定当前 inboundId、账号、公司和会话。targetKey 沿用匹配 thread，或 `boss:chat:` 加 recipient sorted JSON 的 UTF-8 SHA256 前24位 |
| `send-chat` | actionId；普通回复核对编辑器后点一次发送；附件打开原生确认框 |
| `inspect-resume` | 先 open-page resume，核对账号及唯一附件完整文件名，再回聊天；不上传/替换附件 |
| `confirm-resume` / `dismiss-resume` | 核对同一收件人的确认框后确认或取消；每个阶段仅尝试一次 |
| `reconcile-chat` | 原 actionId；新消息 ID、方向、文本和回执必须匹配 |

分享简历前按 policy 校验本地文件 hash，并把平台完整文件名与授权版本绑定；这不证明平台文件字节一致。share_resume 的 `resumeMode: "accept-request"` 只适用于当前明确索要附件且含唯一“同意”的系统 inboundId，不用于电话/微信请求。

“请求已发送/等待对方回复”保留 pending/awaiting-recipient-consent；只有新的附件已发送系统回执才为 attachment-delivered。已知等待不因工具条变化降为 unknown。发送前重新检查新来信、接管、无法归属的我方消息、编辑器和附件版本；已尝试动作只核对。真实验证范围见仓库 VALIDATION.md。

## 账号与动作策略

`select-account-context` 用当前正常页面证据记录用户明确区分的账号：`{"contextId":"local-id","accountLabel":"当前显示名","userDeclaredIndependent":true,"evidence":"用户声明与页面依据"}`。旧限制未归属时补 originalContextId/originalAccountLabel；已有上下文沿用原 ID。显示名不是网站唯一账号 ID，限制归属规则见 browsing-safety。

结构化排除项：`targets.excludedCompanies` 的 name/aliases/reason；`search.excludeHeadhunterPosted`；`search.excludedOpportunityGroups` 的 key/aliases。公司及明确别名可执行匹配，自然语言 targets.exclude 仍由宿主理解。发布者 unknown 不阻断初次沟通，分享简历前再核实；不得编造 direct 身份。

动作携带 accountLabel、targetFacts（company、publisherType、evidence、jobKey）及 accountContextId；网页 Engine 自动注入当前上下文。opportunityGroup 通过 jobKey/thread.jobKeys 关联，不因更换发布者而绕过排除。

长期 draft/ask 下，本次明确授权写入动作 `oneShotAuthorization: {"kind":"reply","platform":"boss","targetKey":"THREAD_ID","inboundId":"MESSAGE_ID","evidence":"用户本次指令"}`。范围必须匹配；oneShotHandover 同时需要具体授权。其他附件授权包含绝对 path/sha256；平台简历 answers 使用 resumeSha256/platformResumeEvidence。完整动作格式见 state-schema。

store begin 只登记 pending，提交前 check-action 重查资料、策略、附件、账号、排除项、接管、时段和暂停。正式业务入口内部完成这些检查，不重复调用。面试 stage/interviewConfirmed 的证据字段见 state-schema。

## 调度记录

`run_ledger.py` 记录宿主运行，不创建调度器。schedule.windows 支持 search-apply/start、reply-check/start/end/intervalMinutes、daily-report/time；启用时记录 startDate，catchUpDays 默认7（1–30）。旧回复轮次合并，跨午夜窗口拆开。due 只列待办，执行顺序按 workflows 的 batchBeforeReply；日报不因批次未满丢失。

```sh
python scripts/run_ledger.py --data-dir DATA due
python scripts/run_ledger.py --data-dir DATA start --token TOKEN --mode reply-check --date YYYY-MM-DD --slot 15:00
python scripts/run_ledger.py --data-dir DATA finish --token TOKEN --key RUN_KEY --status completed --evidence "运行结果"
python scripts/run_ledger.py --data-dir DATA delivered --token TOKEN --key RUN_KEY --receipt "真实宿主交付标识"
```

日报 finish completed 还需 `--artifact logs/<日期>-daily-summary.md`，文件须在数据目录内。completed 是生成，delivered 是实际交付；若只能在最终回答后交付，下一轮核对宿主结果再补记，不提前造回执或盲目重推。中断运行先核对原执行者和 pending/unknown，锁不会超时抢占。

## 输出与诊断

CLI 默认 compact：读取时返回需要的材料，保存决定/发送后只返回 ID、状态和证据；恢复只带详情引用。`--output full` 展开本次分类观察，不额外采集区域。inspect 可传 `{"scope":"list|detail|receipt"}`，聊天用 inspect-chat。

logs/browsing 保留最终观察、首次限制及 wait 计数；logs/operations 仅存 elapsedMs、browserCalls、browserMs、waitMs、recoveryCount、状态和证据引用。执行器计时不含宿主模型思考/工具往返，DOM 调用数不是网络请求数。

```sh
python scripts/doctor.py --data-dir DATA
python scripts/doctor.py --compare-skill /absolute/path/to/installed/job-hunter
python scripts/store.py --data-dir DATA report --date YYYY-MM-DD
```

doctor 只检查本地结构、Python、配置、锁与待核对数量，不输出个人正文或 token，也不清锁。compare-skill 比对副本摘要；可选 --codex 只查询指定 CLI 的 plugin --help。
