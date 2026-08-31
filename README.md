# Voice Pitch Analyzer

语音基频(F0)可视化工具:加载音频文件或直接录音,提取随时间变化的声音基频曲线,支持缩放查看、时间选区和同步播放。

基于 PySide6(Qt6)构建,音高检测使用 [librosa](https://librosa.org/) 的 pYIN 算法,当前面向 **Windows** 平台。

## 功能特性

- **音频加载** — 支持 `wav` / `flac`(原生读取)与 `mp3` / `m4a` / `aac`(经 ffmpeg 转码),支持文件对话框与拖拽导入
- **麦克风录音** — 一键录音,默认输入设备,16 kHz 单声道
- **基频提取** — pYIN 算法,搜索范围 50–500 Hz,输出逐帧 F0 与发声置信度
- **可视化** — 发声段绘制为蓝色实线曲线,未发声帧绘制为底部灰色虚线;时间/频率网格随缩放级别自适应
- **缩放** — 0.25×–16× 七档缩放,支持工具栏按钮、滑块、`Ctrl+滚轮`、`Ctrl+左键拖拽`
- **选区与播放** — 左键拖拽框选时间区间,播放选区或全部音频,红色播放头实时跟随;播放在后台线程执行,界面不卡顿
- **多文件管理** — 文件与录音(`[录音 n]`)保留在会话列表中,点击切换,分析结果即时复用
- **中文界面** — Fusion 风格,简体中文 UI

## 快速开始

项目使用 [pixi](https://pixi.sh) 管理 conda + PyPI 依赖(Python 3.12–3.13,ffmpeg 由 conda-forge 提供)。

```powershell
# 1. 安装 pixi(已安装可跳过)
powershell -c "irm https://pixi.sh/install.ps1 | iex"

# 2. 在项目根目录安装依赖并启动
pixi run run
```

`pixi run run` 会自动创建虚拟环境、安装全部依赖,然后执行 `tasks.run`(`python -m src.main`)。

也可以手动进入环境后启动:

```powershell
pixi shell
python -m src.main
```

> 前置要求:Windows 10/11 x64。无需单独安装 ffmpeg 与 Python,pixi 会一并装好。

## 支持的音频格式

| 格式 | 读取方式 | 加载后采样率 |
| ---- | -------- | ------------ |
| wav / flac | soundfile 原生读取 | 保留原始采样率 |
| mp3 / m4a / aac | 调用 ffmpeg 转码为临时 wav | 统一重采样为 16 kHz 单声道 |

> 注意:文件选择对话框的过滤器中列有 `*.ogg`,但当前加载器尚不支持该格式,选择后会弹出"加载失败"。

## 界面速览

```
┌─ 工具栏:打开文件 | 录音 | 播放 | 放大 / 缩小 / 倍率 ─────────────┐
│ 文件列表 │                                    │
│  (左侧) │  音高曲线视图(选区 / 网格 / 播放头)  │
├─ 控制栏:缩放滑块 ──────────────── 播放位置 0.000s ─┤
└─ 状态栏:加载 / 录音 / 播放状态提示 ──────────────────┘
```

完整操作说明(手势、快捷键、交互行为)见[用户手册](docs/usage.md)。

## 目录结构

```
voice_pitch_analyzer/
├── pixi.toml            # 依赖与任务定义(pixi)
├── pixi.lock            # 锁定依赖版本
├── src/
│   ├── main.py          # 入口:Windows DLL 预加载后启动应用
│   ├── main_window.py   # 主窗口:工具栏 / 文件列表 / 控制 / 状态栏 / 业务逻辑
│   ├── pitch_view.py    # 音高曲线视图:自绘 QGraphicsView(网格/选区/播放头/缩放手势)
│   ├── pitch_detector.py# pYIN 基频提取与频率范围估计
│   ├── audio_loader.py  # 文件加载(soundfile / ffmpeg)
│   ├── audio_recorder.py# 麦克风录音(sounddevice)
│   └── audio_player.py  # 音频播放与播放进度回调(sounddevice)
└── docs/                # 项目文档(见下)
```

## 文档索引(文档驱动)

本项目采用**文档驱动开发**:文档先行、随代码同步更新,改动代码时必须同步修改受影响的文档与 `CHANGELOG.md`。

| 文档 | 内容 | 何时更新 |
| ---- | ---- | -------- |
| [docs/requirements.md](docs/requirements.md) | 需求文档:功能需求、非功能需求、验收标准 | 需求变更时 |
| [docs/design.md](docs/design.md) | 架构与设计:模块划分、数据流、关键技术决策 | 架构/技术选型变化时 |
| [docs/api.md](docs/api.md) | 模块 API 参考:公开函数与类的签名、行为 | 任何对外接口变化时 |
| [docs/usage.md](docs/usage.md) | 用户手册:界面说明、操作手势、常见问题 | 界面/交互变化时 |
| [docs/development.md](docs/development.md) | 开发指南:环境搭建、代码约定、文档同步矩阵 | 工程设施变化时 |
| [docs/roadmap.md](docs/roadmap.md) | 路线图:已知问题、改进计划 | 规划调整时 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 | 每次合入功能/修复后 |

## 已知限制

- 基频分析在 UI 线程同步执行,加载长音频时界面短暂无响应(详见 [roadmap](docs/roadmap.md))
- 会话内容不持久化:导入的文件与录音关闭后即丢弃,录音暂不支持导出
- 仅支持 Windows;macOS / Linux 未启用

## 许可证

尚未设置开源许可证(详见 [roadmap](docs/roadmap.md))。
