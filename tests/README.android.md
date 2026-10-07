# 手机端功能回归

这些测试使用隔离的临时数据、匿名响应和 UI 结构。不会连接手机、请求招聘平台、调用真实 Jev 或外发消息。报告使用 `--report` 保存到仓库外的会话输出目录。

## 运行

使用已经安装 `tests/requirements.txt` 的 Python（包含 pytest 与 Android 测试依赖）。测试使用函数、fixtures、原生 `assert`、`pytest.raises` 和参数化场景。原生图标规则直接编译并运行正式 `SendTarget.java`，需要 JDK；无需 Android 手机或模拟器。

```text
python tests/run_android.py all --jdk JDK
python tests/run_android.py communication
python tests/run_android.py acquisition recovery
python tests/run_android.py native --jdk JDK
python tests/run_android.py --changed --jdk JDK
python tests/run_android.py --changed --list
python -B -m pytest -c pytest.ini tests/test_android_navigation.py -q
python -B -m pytest -c pytest.ini tests -m "android and communication" -q
python -B -m pytest -c pytest.ini tests -m android --jdk=JDK -q
```

日常改动运行受影响的功能组。`--changed` 根据当前 Git 改动选组；修改共享 `store.py`、`policy_rules.py` 时附带已有状态、策略和聊天契约测试。Web 脚本保持原实现。改测试公共夹具、领域公共类型时运行手机端全部功能组。只改文档不启动测试。

`pytest.ini` 注册功能 markers 并关闭仓库内缓存。`conftest.py` 使用 `tmp_path` 隔离每条测试，将临时文件放到仓库外的会话目录；可通过 `--session-output=SESSION_OUTPUT` 指定。分组入口的 `--report` 写 pytest JSON 结果，不在源码目录存放运行产物。

改 Java 助手后，另用 `build_native_input.py --sdk SDK --jdk JDK --output SESSION_OUTPUT/native-text.jar` 确认真实 Android SDK 编译。新增 App 界面或协议格式时采一次现场样本，脱敏为固定夹具，再放入对应组；同类回归以后由自动测试检查。

## 功能与今天遇到的问题

| 功能组 | 覆盖的行为与问题 | 主要代码 |
|---|---|---|
| device | 固定 ADB server、transport 与物理身份分离；超时保留原因；锁屏、验证页不能当 BOSS 页面；实时读取覆盖分辨率和版本 | android_device.py |
| navigation | 第三次返回后漏检查；列表名称截断；消息行刷新后旧坐标点入相邻会话；打开后公司校验；折叠聊天卡没有岗位标题；同一我方气泡才算送达 | android_ui.py |
| acquisition | 完整 JD 一次 Jev；列表摘要不可当全文；未知模型调用不重复；换批未变 JD 复用决定；内容或判断上下文改变才失效 | android_application.py、android_repository.py、jev.py |
| delivery | 原子去重、并发仅一次权限；持久化后才点击；崩溃、断线、未知结果只核对；原 marker 不受全局覆盖；额度、终态、证据归属及日志/存储故障 | store.py、android_application.py |
| communication | 最新消息改变取消草稿；未知助手结果继续核对；未发送草稿修改可重新审阅；成功回复重跑复用原结果 | android_application.py |
| native | Android 16 缺 base.jar 的假 OK；真实方法执行状态；取消与一次 commit；损坏/错动作 readiness；无文字发送图标、表情、附件、歧义及不可见控件 | android_text.py、NativeText.java、SendTarget.java |
| recovery | 分辨率、版本改变保留 JD、决定和动作；旧 plan 多余字段不重建批次；显式迁移；新批次仍能打开旧会话、核对旧动作 | android_repository.py、android_application.py |
| protocol | 实际 greet 加密前缀仍为诊断；支持的 ARC4/BZP/LZ4 格式及损坏格式；非零业务拒绝；迟到回执、无关路由错误；不保存请求凭据或无关接口正文 | boss_protocol.py、android_capture.py、android_receipts.py |
| proxy | 恢复 :0 与派生 host/port；恢复失败保留监听；识别原 CLI 进程退出 | android_capture.py、android_receipts.py |
| policy | 云衍 JD 的“能够接受出差”漏检；无需出差可通过；云产品提及不能当雇主，大厂雇主必须排除 | policy_rules.py |
| workflow | 搜索筛选 → 全文 → 一批 Jev → 持久化投递 → 消息及主动说明；只读流程不创建发送动作；重复主流程不外发；定位失败继续其他候选；跨批额度与消息检查计数 | 完整手机用例 |

测试断言来自业务结果：外发权限和点击次数、Jev 调用次数、当前账号、动作归属、保留下来的事实及实际计数。协议测试通过不表示未知 greet 格式已能解码；它确保该格式不会被误报成成功或业务拒绝。

框架接口依据 [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)、[参数化](https://docs.pytest.org/en/stable/how-to/parametrize.html) 和 [API 参考](https://docs.pytest.org/en/stable/reference/reference.html)。
