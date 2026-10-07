# Kimi 网页通道

仅网页操作使用 Kimi Browser Extension / WebBridge；Android App 见 [android.md](android.md)。首次网页任务读取当前环境的 kimi-webbridge 技能，业务操作经 [browser_actions.py](../scripts/browser_actions.py)，传输由 webbridge_client.py 内部完成。策略 required/preferred 为 kimi-webbridge、allowFallback 为 false；旧配置仅有 preferred 时仍固定 Kimi。

## 会话与页面恢复

Kimi session 是 daemon 会话标识。复用本任务归属的 session，不拿 CUA 标签 ID 替代，也不接管其他任务的标签。缺通道时保存断点、继续本地工作；不自动换浏览器或重启 daemon。

1. 新运行或中断后先 `resume-context` 读本地断点与限制，无需锁/session。
2. 获取运行锁后用 `inspect-session` 查当前 session 的真实 URL、active/borrowed，再 `select-page {"page":"jobs|chat|resume"}` 选择唯一匹配页。必须核对实际返回 URL，同站错页不算成功。
3. `chat-job-detail` 打开的详情用 `select-page {"page":"detail","key":"boss:JOB_ID"}` 选择。borrowed 标签仅在清单证明该详情 active 时使用 active 选择；不借用用户其他标签。
4. `inspect` 报 tab was closed / navigate first 时，经原 session 执行 `recover-page`。已有唯一职位页可被动选中；新开页面仍须通过访问限制检查。多页无法归属时保留现场。未决 submit 先 reconcile，不能用恢复重发。
5. 正常错页用 `open-page` 导航，回列表用 `restore-filters` 并核对选项。保留候选、backlog、成功及 unknown。

`focus-page` 临时启用当前页 focus emulation 并 bringToFront，用于已排除访问限制的后台加载停滞。导航/切页自动 release-focus，结束时也释放。visible 只证明当次观察。菜单自动收起时，仅对上一观察存在的选项重新展开同一菜单一次；仍失败保存原因，不用旧坐标点其他菜单。

`recover-page` 成功不代表账号/筛选正确或限制解除。原账号已有正常页面后先 inspect，再 `clear-access-block {"evidence":"恢复依据"}` 核对原账号/session和 retryNotBefore；到期本身不足以解禁。规则见 [browsing-safety.md](browsing-safety.md)。

## 登录恢复

有效登录直接复用；普通登录失效才恢复。访问限制未满足恢复条件时不触发登录、刷新或试换账号。仅通过已适配入口执行，缺能力记录具体缺口，不能裸调用传输绕开业务检查。

用户已授权本人设备辅助本次网站登录时，按 `policy.browser.androidConfig` 及其 skillPath 调用 Android 技能。限定站点、号码、请求后的时间窗和用途；验证码只在内存中传给当前表单，不落日志、画像、请求文件。未连接、无权限或无法可信匹配时交用户完成。

锁屏/熄屏时先尝试短信 provider；空结果再按 Android 技能从短信列表检查本次最新消息，不沿用旧会话或掩码通知。需要解锁时只用已有授权配置，结束恢复本轮改变的锁屏状态；用户正在操作则保留现场。插件不保存解锁凭据。网页登录有效时不请求验证码或读取短信。

可见拼图/滑块按当前工具允许的正常截图与拖动操作处理，核对实际结果；工具限制、失败或本人身份确认时保留现场交接。登录恢复后先核对 pending/unknown。只读报告不主动改变账号登录状态。

## 页面操作

- 依据当前 DOM/语义引用或截图定位；导航、切会话、滚动或弹窗后重新观察。截图像素与 CDP 的 CSS 坐标有缩放差异时先换算。
- 填写后核对真实文本，发送前确认唯一目标和按钮。不要用未验证的 Enter 提交。
- 被动观察不夹带点击、导航、滚动或 fetch；响应适配器只读 UI 已产生的响应。招聘接口调用/重放不属于采集。
- 等待按身份、内容、回执条件；不依赖随机延迟证明安全。具体业务入口、输入格式和输出范围见 [runtime.md](runtime.md)。
- 通道缺失仅限制对应能力，不伪造网页成功。

## recon

记录工具/session、平台、最少账号标识、检查时间，以及登录、搜索、完整 JD、所需会话的 available/unavailable/unverified 和原因。零搜索结果、无既有会话不等于故障。

可填搜索框，不填消息/申请表，不试发招呼、回复或附件。只验证用户本次需要的能力；未适配站点只分析已有材料，见 [platform-generic.md](platform-generic.md)。
