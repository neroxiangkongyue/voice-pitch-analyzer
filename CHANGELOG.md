# 更新日志(Changelog)

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式,
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划中

- 基频分析移入工作线程并增加进度反馈(见 [roadmap](docs/roadmap.md) B-8)
- 支持 .ogg 加载,消除过滤器与加载器白名单不一致(B-4)
- 确定开源许可证

## [0.2.0] - 2026-08-31

### Added

- 文件列表多文件管理:会话内保留多个文件与录音(`[录音 n]`),点击切换并复用缓存的分析结果;重复导入同一文件直接切换,不重复分析
- 播放结束(含自然播完)后界面自动复位:按钮、播放头与位置标签
- 录音停止后的分析环节增加异常保护("分析失败"对话框)

### Changed

- 播放改为非阻塞:基于 `sd.OutputStream` 回调流 + 后台线程,UI 以 30 ms 定时器轮询播放位置;后台侧不触碰 Qt 对象
- `PitchView.set_data` 现在会重置当前选区(选区以秒存储,跨数据集无意义)
- 清理未使用的导入与死代码(旧播放定时器链路、`_draw_pitch_curve` 中未使用的临时 line item)

### Fixed

- 播放调用不存在的 `sd.Player` API:v0.1.0 点击"播放"必然抛 `AttributeError`,播放从未真正可用,已改用 `sd.OutputStream` 实现
- 选区播放时播放头越界不显示、位置标签偏大(时间二次叠加;`current_time` 统一为音频时间轴绝对时间)
- 重绘(缩放/切文件)后选区高亮不可见:场景清空后选区矩形未重新加入
- 播放头线在场景中重复 `addItem` / 移除未加入的 item(消除 Qt 告警与潜在崩溃)

### Docs

- 建立文档驱动文档集:README、docs/(requirements / design / api / usage / development / roadmap)、CHANGELOG

## [0.1.0] - 2026-08-24

首个可用版本。

### Added

- 音频文件加载:wav / flac(soundfile 原生)与 mp3 / m4a / aac(ffmpeg 转码 16 kHz 单声道),支持对话框多选与拖拽导入
- pYIN 基频提取(librosa,50–500 Hz)与自动频率轴适配(5%/95% 分位数 + 边距)
- 自绘音高曲线视图(QGraphicsView):发声段蓝色实线、未发声帧灰色虚线、时间/频率自适应网格
- 七档缩放(0.25×–16×):工具栏按钮、滑块、`Ctrl+滚轮`、`Ctrl+左键拖拽` 手势
- 时间选区:左键拖拽框选,支持"播放选区"右键菜单
- 播放与实时播放头、播放位置显示
- 麦克风录音(默认设备,16 kHz 单声道)并自动分析
- pixi 工程:Python 3.12–3.13、conda-forge ffmpeg、`run` 任务
- Windows 启动器:PySide6 Qt DLL 预加载(`python -m src.main`)

[Unreleased]: https://github.com/placeholder/voice_pitch_analyzer/compare/0.2.0...HEAD
[0.2.0]: https://github.com/placeholder/voice_pitch_analyzer/compare/0.1.0...0.2.0
[0.1.0]: https://github.com/placeholder/voice_pitch_analyzer/releases/tag/0.1.0
