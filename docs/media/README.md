# 展示素材

动画、静态图和架构图分别提供 `zh-CN` 与 `en` 两个版本。中文首页加载中文素材，英文 README 加载英文素材。

动画为程序绘制，场景使用示例资料。`generate.py` 定义项目场景，`motion.py` 负责绘制与动效，`zh-CN.json` 保存中文文案。架构图是可直接编辑的 SVG。

## 重新生成

在独立 Python 环境中，从仓库根目录执行：

```sh
python -m pip install -r docs/media/requirements.txt
python docs/media/generate.py
```

默认生成两种语言；只生成一种时添加 `--language zh-CN` 或 `--language en`。

Pillow 仅用于制作素材。中文字体按系统选择微软雅黑、Noto Sans CJK 或苹方，也可通过 `SHOWCASE_FONT` 与 `SHOWCASE_FONT_BOLD` 指定本机字体文件；字体不随仓库分发。

输出为 1120 × 640 GIF（12 fps）与静态 PNG。设置 `SHOWCASE_REVIEW` 可额外输出六帧检查图。
