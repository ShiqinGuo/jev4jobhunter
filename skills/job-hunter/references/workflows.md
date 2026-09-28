# 按意图执行

各模式共用 SKILL.md 的数据、批量判断和外发规则；这里只补充任务差异。Android CLI 自动管理锁，网页和直接 store 写入由调用方获取并释放。参数见 [runtime.md](runtime.md)。

## setup：初始化或更新

1. 确定数据目录与已有资料，执行 store init；旧状态按 [profile-schema.md](profile-schema.md) 迁移，不覆盖事实、策略或历史。
2. 从已有简历提取事实和来源；只按 [intake.md](intake.md) 补问会影响本轮决定的缺项。profile 存事实，policy 存条件与授权，未知项不补造。
3. 保存用户已确定的更正，按需准备 opener 草稿。浏览器 recon 不是本地 setup 的前置条件。

用户另行要求修改在线简历时逐段保存并重新打开核对，工作/项目/技能分别填写，日期按材料或明确指令。已确认的招呼语直接复用，不继续润色追加。

## search 与 daily

search 只发现和比较；daily 处理用户指定的新消息、候选和待办。--dry 传递到全部分支，不产生招聘平台外发。

先读策略、断点、平台限制与未决动作。pending/unknown 先对原动作核对，再推进新任务。当前真实限制按 [browsing-safety.md](browsing-safety.md) 处理；普通登录失效按 drivers 恢复。

网页使用 browser_actions，真机按 [android.md](android.md)。设置并核验来源、城市和原生筛选，去重/排除后收齐完整 JD，一次 Jev 决定发送队列。列表批次处理完才继续加载；推荐流不替代指定搜索。保存查询、筛选、候选去向和断点，摘要区分成功、待核对、草稿与缺口。

### 批量投递后查消息

`search.batchBeforeReply` 表示查看会话的间隔，例如50，不是详情组大小或每日额度。同一岗位招呼和附件只算一个新增成功岗位；失败、pending、unknown 不充数。起点、累计成功数与游标存 state.scheduler，跨运行继续。

达到间隔后集中回复，再继续下一批。未满时普通定时回复让位；发送额度耗尽、结果确实耗尽，或只有搜索功能故障且聊天可用时，可提前处理会话。用户要求立即处理某会话时按最新指令；访问限制仍生效。日报按时汇总，注明未同步范围。实际平台额度模式用 dailyLimits=null，不把大整数当无限。

等待用户决定时先完成独立工作、保存断点并释放锁；收到回复后重新获取并检查现场，不持锁无限等待。

## reply：回复具体消息

网页用 open-conversation-and-wait → 审阅 → reply-and-verify；附件走 runtime 的原生确认。Android 当前仅适配新沟通，聊天跟进尚未适配。

1. 唯一定位公司、招聘者、岗位、会话及最新 inboundId，读取必要历史。未读标记只用于发现，不能用同文本/日期代替消息身份。
2. 检查 humanTakenOver/needsReview、旧动作和未归属我方消息；人工接管时不自动回复。用户本次明确要求可用 oneShotHandover，保留长期暂停。
3. 按完整 policy、JD、profile 回答实际问题，说明真实匹配点。缺事实保留待答，约面、薪资、联系方式等承诺按具体授权处理，不先发过渡话术。
4. 已授权 communication.autoShareResumeAfterReply 时，对方正常回复或表达意向后可分享授权附件；拒绝/错配不触发，已成功或未知的分享不重复。
5. 记录匹配回执和 lastInboundId；对同一来信不能通过改变 reply/commitment 的 kind 重复回复，补充第二条需要独立的新指令或事件。

## apply：用户选中的岗位

“投这几个”只覆盖指向的职位。核对当前完整 JD、原判断、最新排除项与授权，再用 submit-reviewed-detail（网页）或 apply-batch（Android）执行。已有有效完整 JD 不重复评价，正文/资料/策略变化时才重审。

正文、附件或表单材料先在本地准备；用户明确的申请指令覆盖相应上传/提交，不重复询问。未适配的直链、ATS 或上传入口记录缺口，不能伪造列表来源。平台 greet 与正式 application 分开统计，结果以回执为准。

## report 与 resume

report 默认只读本地，优先呈现待用户处理事项和 pending/unknown，标明最后同步时间；用户要求实时更新时再读取平台。面试邀请和双方排期确认分别记录证据，不把索简历、已读、回复或我方约面当成面试成功。

resume 仅对唯一匹配线程清除 humanTakenOver/needsReview 并刷新上下文；无匹配或多匹配时列必要候选。恢复接管沿用原授权。暂停全部自动运行时按用户要求同步暂停相关宿主任务。

## schedule：宿主调度

只在用户要求定时、重复运行或修改计划时调用宿主真实调度工具，daily 不创建/重建任务。按真实任务 ID 与 data-dir 查找已有计划，更新时保留未要求改变的字段。

提示仅引用 Skill、data-dir、模式（含 --dry）、当前 policy、目标与交付要求，不复制城市/黑名单/授权全文。核对宿主支持的时区、持久性和电脑/会话运行条件，有成功回执后才记 state.scheduler；没有调度能力时交付任务描述并说明未创建。

轮次、漏跑补报、生成与交付用 runtime 的 run_ledger 记录。通知使用宿主任务结果/当前对话；外推仅按明确授权使用宿主连接器，失败不改变已提交动作状态。
