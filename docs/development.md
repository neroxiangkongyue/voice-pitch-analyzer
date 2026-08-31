# 开发指南(Development)

> 状态:v0.2.0。面向开发者:环境搭建、日常命令、代码约定与文档同步规则。

## 1. 环境搭建

### 前置要求

- Windows 10/11 x64
- [pixi](https://pixi.sh)(包与环境管理器,安装一次即可)

Python、ffmpeg 及全部 PyPI 依赖均由 pixi 按 `pixi.toml` / `pixi.lock` 安装,无需手工安装。

### 常用命令

```powershell
pixi install            # 创建/更新虚拟环境(.pixi/envs/default)
pixi run run            # 运行 tasks.run(python -m src.main),首次自动装环境
pixi run python -m src.main   # 等价方式
pixi shell              # 激活环境到当前 shell,之后可直接 python xxx.py
pixi list               # 查看已安装依赖
pixi add <pkg>          # 添加 conda 依赖(会更新 pixi.toml 与 pixi.lock)
pixi add --pypi <pkg>   # 添加 PyPI 依赖
```

> 必须**通过 pixi 运行**或 `pixi shell` 后再运行:ffmpeg 在 pixi 环境内;`src/main.py` 的 DLL 预加载也依赖正确的包布局。
> `pixi.toml` 中配置了清华/阿里/豆瓣 PyPI 镜像以加速国内网络;海外环境可按需移除 `[pypi-options]` 段。

## 2. 运行入口与启动方式

统一入口:`python -m src.main`。不要直接运行 `python src/main_window.py`——`src/main.py` 中的 Qt DLL 预加载必须先于 PySide6 导入执行(见 [design §4.1](design.md#41-windows-dll-预加载mainpy))。

## 3. 代码结构速览

详见 [design.md](design.md) 与 [api.md](api.md)。新增代码时:

- 算法(信号处理类)进 `pitch_detector.py` / `audio_*.py`,不写进 UI 层;
- 界面文案使用简体中文,代码标识符使用英文;
- 类型标注(`np.ndarray | None` 风格)与现有代码保持一致。

## 4. 文档驱动工作流(重要)

本仓库**文档与代码同权重**。合入任何改动的检查清单:

1. 需求变化 → 更新 [requirements.md](requirements.md);
2. 架构/技术选型/数据流变化 → 更新 [design.md](design.md);
3. 函数/类签名或行为变化 → 更新 [api.md](api.md);
4. 界面/交互/文案变化 → 更新 [usage.md](usage.md);
5. 已修复的已知问题 → 从 [roadmap.md](roadmap.md) 移除;新发现的限制/缺陷 → 记入 roadmap;
6. 一律追加 [CHANGELOG.md](CHANGELOG.md) 条目(遵循 Keep a Changelog 与语义化版本);
7. 文档中的"状态: vX.Y.Z"页眉随版本推进更新。

## 5. 提交规范

使用 Conventional Commits(与现有历史一致):

```
feat: 新功能
fix: 缺陷修复
docs: 仅文档
refactor: 重构(不改行为)
chore: 构建/杂项
```

## 6. 已知实现细节与陷阱

- **线程模型**:播放由 `AudioPlayer` 的 PortAudio 回调 + 后台等待线程驱动,UI 通过 `_on_play_tick`(30 ms `QTimer`)轮询;**不要在音频线程里触碰任何 Qt 对象**(见 [design §4.6](design.md));
- **分析仍为同步**:pYIN 分析在 UI 线程执行,长文件加载时界面短暂无响应属预期(改进见 [roadmap](roadmap.md));
- **时间语义**:`AudioPlayer.current_time` 是音频时间轴上的绝对秒数(`start + elapsed`),UI 层直接使用,不要再叠加偏移;
- **缩放档位表重复**:`[0.25, 0.5, 1, 2, 4, 8, 16]` 在 `main_window` 与 `pitch_view` 中各有一份,改动需两处同步;
- **启动方式**:`python -m src.main` 是唯一受支持的启动方式;
- **格式白名单不一致**:文件对话框过滤器含 `*.ogg`,而 `audio_loader` 不支持;
- **`.gitignore` 已忽略音频文件**(`*.mp3/*.wav/...`)与 `.pixi/`,测试音频请放在仓库外或自行处理。

## 7. 测试与打包(现状)

- 当前无自动化测试框架与打包发布流程(见 [roadmap](roadmap.md));
- 手工冒烟路径:加载 2 个 wav/mp3 → 列表点击切换 → 缩放全档位 → 框选并播放选区(观察播放头位置是否正确)→ 播放中缩放/切换文件 → 录音 3 秒停止分析 → 切回上一文件。
