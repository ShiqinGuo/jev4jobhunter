# 贡献与验证

Python 3.10+ 用于核心逻辑测试；Node.js 22 与固定版本 Playwright/Chromium 用于离线 DOM 行为测试。首次准备开发依赖：

```sh
npm ci
npx playwright install --with-deps chromium
python -B -m unittest discover -s tests -v
```

浏览器样例运行时断网，不连接招聘平台。缺少 Node 或 Chromium 时，本地会明确跳过 DOM 测试；CI 将缺依赖视为失败。规则测试覆盖允许和拒绝两侧及中断恢复，不只匹配文案。插件运行本身不依赖这些 npm 开发包。

发布包可用 `python -B scripts/package.py --output ../job-hunter-0.4.0.zip` 构建，附逐文件 SHA-256 清单。

浏览器改动使用脱敏页面样例或独立授权测试账号；测试不得自动投递或发真实消息。保留工具版本、页面证据和实际失败边界。

个人简历、聊天、手机号、短信、设备配置、策略备份和 state.json 不属于源码。优先附 doctor 输出和脱敏步骤；doctor 不输出个人正文和运行 token。

字段归 runtime.md，平台经验归对应 platform 参考。个人偏好设计为可选配置。代码按 MIT 许可证发布；贡献同样采用该许可证。
