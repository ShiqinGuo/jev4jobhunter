# Android 真机

使用已授权的 ADB 真机、BOSS 官方客户端及可信用户证书。依赖见 `scripts/android-requirements.txt`。设备配置保存在 DATA/android.json：`adb`、`serverPort`、`serial`、`manufacturer`、`model`、`python`、`caDir`，可选 `proxyPort`、`cityInput`（城市到拼音）。证书私钥不复制进插件。

## 默认入口

只读采集与能力测试直接用 `preview`，不写临时编排脚本：

```text
python scripts/android_actions.py --config DATA/android.json --data-dir DATA preview --source recommendation --city 杭州 --query Python --count 15
```

主动搜索用 `--source search --query python开发`。跨运行去重可传 `--exclude-file FILE`，格式 `{"keys": [...]}`；同一数据目录的既有采样自动去重。中文查询复用搜索历史/建议；缺少时在手机正常输入一次，不安装输入法。

命令自动完成：启动采集并重启 App → 核对账号 → 切入口/城市 → 设置并核验筛选 → 固定滚动收齐完整 JD → 一次 Jev → dry-run 队列 → 恢复代理。不会外发，不接受 `--send`。返回数量、耗时及报告引用，完整证据在 DATA/android。工具返回运行中的 session 时等待原进程；不要重复启动。Jev 先记录 pending，已落盘结果恢复复用，未知结果不盲目重调。

筛选来自 `policy.search.androidFilters.recommendation` / `.search` 的 `salaryLabels`、`experienceLabels`、`bossActivity`。首页薪资单选：脚本轮换指定的原生档位、跨档去重，合并一次 Jev；不创建自定义薪资。首页没有独立“经验不限”，不以“全部”代替。换城后重设筛选；首页以控件填充色核验，搜索读取选中属性。截图用于控件证据，岗位正文只读响应。

## 单步操作与恢复

同一 CLI 的单步入口保留用于排查：

| 操作 | 用途 |
|---|---|
| start / stop | 启停被动采集；start 后重启 App，让既有连接进入代理 |
| bind-account | 在“我的”等可读取账号显示名的页面绑定批次 |
| arm-search / read-batch | 先标记，再从 UI 触发列表，接收对应响应 |
| calibrate-scroll / scroll-next | 两次加载校准固定手势，后续复用；设备、分辨率或版本变化重新校准 |
| collect-details / evaluate-batch | 收齐当前批次完整 JD，一次布尔判断 |
| apply-batch | 默认 dry-run；仅已有外发授权时加 --send |

CLI 自动获取并在 finally 释放 store 运行锁，不要在调用前手动 lock acquire。发送复用 outbox：点击前写 pending，匹配动作的成功响应才记 succeeded，未知则停止且不重发。正常进入聊天不算送达。

`android_capture.py` 只解码 UI 已产生的响应，不调用或重放招聘接口。缺响应不等于空列表。保存职位必要字段及回执，不保存登录数据。停止时先写 `http_proxy=:0`（原先无代理时）并核验派生主机为空，再关闭代理；仅删除该键会留下断网代理。拔 USB 前执行 stop。
