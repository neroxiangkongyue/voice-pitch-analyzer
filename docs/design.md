# 架构与设计(Design)

> 状态:v0.2.0。描述系统的模块划分、数据流与关键技术决策。
> 架构或技术选型变化时须同步更新本文档与 [CHANGELOG.md](../CHANGELOG.md)。

## 1. 总体架构

单进程桌面应用,自上而下分为四层:

```
┌─────────────────────────── 入口层 ───────────────────────────┐
│ main.py — Windows DLL 预加载,调用 main_window.main()         │
├─────────────────────────── UI 层 ────────────────────────────┤
│ main_window.MainWindow      主窗口、工具栏/控制栏/状态栏/业务编排 │
│ pitch_view.PitchView        自绘曲线视图(选区/播放头/缩放手势) │
├────────────────────────── 算法层 ────────────────────────────┤
│ pitch_detector.extract_pitch / detect_frequency_range         │
├────────────────────────── 音频 IO 层 ────────────────────────┤
│ audio_loader.load_audio      文件加载(soundfile / ffmpeg)     │
│ audio_recorder.AudioRecorder 麦克风录音(sounddevice)          │
│ audio_player.AudioPlayer     播放(sounddevice)                │
└──────────────────────────────────────────────────────────────┘
```

依赖方向单向:UI 层 → 算法层 / 音频 IO 层;算法层与音频 IO 层互不依赖,也不反向依赖 UI。

## 2. 数据流

```
                ┌─────────── 导入 ────────────┐
   音频文件 ──▶ │ load_audio()                │──┐
   麦克风   ──▶ │ AudioRecorder.start/stop()  │  │ (samples: float32 单声道, sr)
                └─────────────────────────────┘  ▼
                                   ┌─────────────────────────────┐
                                   │ extract_pitch()             │
                                   │  librosa.pyin               │──▶ (times, f0, confidence)
                                   └─────────────────────────────┘            │
                                   ┌─────────────────────────────┐            ▼
                                   │ detect_frequency_range(f0)  │──▶ (fmin, fmax)
                                   └─────────────────────────────┘            │
                                                ┌─────────────────────────────┘
                                                ▼
                     MainWindow._docs: list[AudioDocument](音频+分析结果缓存)
                     MainWindow._doc 为激活文档,调用 PitchView.set_data()
                                                │
                                                ▼
                              PitchView._redraw():网格 + 曲线 + 选区 + 播放头
                                                ▲
   播放:AudioPlayer.play()(非阻塞,后台线程)              │
        UI 定时器(30 ms)轮询 player.current_time ────────┘
        (更新播放头与位置标签)
```

会话状态由 `AudioDocument` 列表承载:`MainWindow._docs` 持有全部已导入文档(音频样本 + 分析结果),`_doc` 指向当前激活文档。同一文件重复导入直接切换,不重复分析;切换文档时播放自动停止,选区重置(选区以秒存储,跨数据集无意义)。`PitchView` 只接收当前文档的引用用于绘制,不修改主窗口状态(缩放与选区播放通过调用 `parent._zoom_to_index` / `parent._start_playback` 反向联动,见 §4.5)。

## 3. 模块职责

| 模块 | 职责 | 不负责 |
| ---- | ---- | ------ |
| `src/main.py` | 在导入 PySide6 之前预加载 Qt DLL(见 §4.1);组装 `QApplication` 并进入事件循环 | 任何业务逻辑 |
| `src/main_window.py` | 界面搭建、用户动作处理、各模块编排、多文档状态(`AudioDocument` 列表) | 绘制细节、音频算法 |
| `src/pitch_view.py` | 场景绘制(网格/曲线/选区/播放头)、鼠标与滚轮手势、拖放转发 | 音频解码、分析 |
| `src/pitch_detector.py` | pYIN 基频估计、显示频率范围估计 | 界面 |
| `src/audio_loader.py` | 按扩展名分发:wav/flac 用 soundfile;mp3/m4a/aac 调 ffmpeg 转 16 kHz 单声道临时 wav | 录音、播放 |
| `src/audio_recorder.py` | sounddevice `InputStream` 回调式采集,聚合为 float32 数组 | 播放、文件 IO |
| `src/audio_player.py` | `sd.OutputStream` 回调播放指定片段,后台线程等待结束并释放资源;维护 `current_time` 供 UI 轮询 | UI(经轮询解耦) |

## 4. 关键技术决策

### 4.1 Windows DLL 预加载(`main.py`)

在 `import` 任何 PySide6 模块**之前**,用 `ctypes.WinDLL` 以 `LOAD_WITH_ALTERED_SEARCH_PATH (0x8)` 依次预加载 `Qt6Core.dll`、`Qt6Gui.dll`、`Qt6Widgets.dll`,并把 PySide6 包目录加入 `PATH` 与 `os.add_dll_directory`。

原因:pixi/conda 环境下 Python 对 Qt DLL 的搜索顺序偶发失败(找不到 Qt6Core.dll),显式预加载绕开该问题。因此**必须以 `python -m src.main` 方式启动**,直接运行 `main_window.py` 会绕过预加载。

### 4.2 音频解码策略(`audio_loader.py`)

- `soundfile`(libsndfile)原生支持 wav/flac,直接读取并混为单声道,**保留原始采样率**。
- mp3/m4a/aac 由外部 `ffmpeg` 进程转码为 **16 kHz 单声道**的临时 wav(`原文件名.tmp.wav`,60 s 超时,finally 中删除)后读取。
- ffmpeg 依赖由 pixi 从 conda-forge 安装进环境,保证 `PATH` 可用。
- 统一输出 `float32` 单声道 numpy 数组,后续算法与播放不再关心来源差异。

### 4.3 基频算法:`librosa.pyin`(`pitch_detector.py`)

- 选择 pYIN:概率化的 YIN,对单声道人声基频估计准确且自带**逐帧发声置信度**,可直接用于区分发声/未发声帧,无需额外的 VAD。
- 参数:`fmin=50 Hz`、`fmax=500 Hz`(覆盖绝大多数人声基频)、`frame_length=2048`、`hop_length=frame_length//4=512`(约 32 ms 帧移 @16 kHz)。
- 输出 `f0` 中 `NaN` 表示该帧未检出音高;`voiced_confidence` 为 0–1 置信度。
- 显示范围估计 `detect_frequency_range`:取有效 F0 的 5%/95% 分位数,加 10% 边距,夹取在 [50, 2000] Hz,避免离群值撑爆纵轴;无有效数据时回退 (50, 500)。

### 4.4 渲染:Qt Graphics View 自绘(`pitch_view.py`)

- 未使用 pyqtgraph 的 PlotWidget(pyqtgraph 仅作为依赖保留并设置全局背景色),而是基于 `QGraphicsView` + `QGraphicsScene` 自绘。原因:需要高度自定义的交互——左键拖拽选区、`Ctrl+拖拽`手势缩放、自绘播放头与自适应网格,PlotDataItem 的默认交互模型不够贴合。
- 坐标换算:视图内预留边距(左 60 / 上 30 / 右 20 / 下 30 px)得到绘图区 `_view_rect`;时间→x、频率→y 均为线性映射,`显示时长 = 总时长 ÷ zoom`。
- 缩放档位固定为 `[0.25, 0.5, 1, 2, 4, 8, 16]`,主窗口滑块索引与该表对齐。
- 网格自适应:缩放 ≥4 → 0.1 s 步长,≥2 → 0.5 s,否则 1 s;频率步长按量程取 25/50/100 Hz。
- 曲线:发声帧用蓝色 `QGraphicsPolygonItem` 折线;未发声帧(含低置信度帧)画在 `fmin` 高度的灰色虚线折线上,提示"此处有帧但无声"。
- 数据量:每次缩放/数据变更全量重绘场景;当前实现为逐帧折线,未做降采样(见 roadmap)。

### 4.5 缩放联动模型

`PitchView` 缩放手势(`Ctrl+滚轮`、`Ctrl+拖拽`)不自行改状态,而是回调 `parent._zoom_to_index(index)`,由 `MainWindow` 统一更新滑块、倍率标签和视图,保证所有缩放入口(按钮/滑块/手势)状态一致。

### 4.6 播放模型:回调流 + 后台线程 + UI 定时轮询

`AudioPlayer.play()` 立即返回:为片段创建 `sd.OutputStream`(512 帧/块,16 kHz 下约 32 ms)并以 PortAudio 回调喂数据,回调内逐块推进 `current_time`(音频时间轴绝对秒数),片段播完或被停止时抛 `CallbackStop`;一个守护线程等待流结束并负责 `stop()/close()` 清理。`stop()` 置位停止标志并 join 该线程(至多 0.5 s),返回时资源必然已释放、`is_playing` 必为 False。

UI 侧由 `MainWindow._on_play_tick`(30 ms `QTimer`)轮询 `is_playing` / `current_time`,在 **UI 线程内**更新播放头与位置标签,并在检测到播放结束(含自然播完)后复位按钮。PortAudio 回调线程与等待线程都不触碰任何 Qt 对象,规避跨线程 UI 调用。

> v0.1.0 的实现调用的是并不存在的 `sd.Player` API(点击播放必然抛 `AttributeError`,播放从未真正工作),且在 UI 线程内同步轮询阻塞事件循环;v0.2.0 一并修正。

### 4.7 录音模型

`AudioRecorder` 用 sounddevice `InputStream`(16 kHz、单声道、blocksize 4096)回调采集,回调内仅做 `copy()` 入 list(音频线程内不做重活);`stop()` 时拼接并混为单声道 `float32` 返回。

### 4.8 会话内多文档模型(B-3)

`MainWindow` 以 `AudioDocument` dataclass 承载一个"文档":标题、音频样本、采样率、分析结果(times/f0/confidence)与频率轴范围(fmin/fmax)、文件路径(录音为空串)。`_docs` 列表保存会话内全部文档;文件列表控件项通过 `Qt.ItemDataRole.UserRole` 数据持有对应文档引用。

- 加载已存在的路径时不重复分析,直接切换(`_load_file` 去重);
- 录音停止后生成 `[录音 n]` 文档入列并激活,因此录音结果在会话内可随时切回;
- 切换文档(`_activate_document`)复用缓存分析结果、仅重绘视图,并复位缩放(1×)、选区与播放状态;
- 所有文档仅存在于内存,不持久化。

## 5. 状态与生命周期

- 应用生命周期:进程启动 → `main.py` 预加载 DLL → `MainWindow.__init__` 构建界面 → 事件循环。
- 会话状态:`_docs` 中的所有文档(音频 + 分析结果)仅在内存中,**不持久化**;关闭即丢弃(录音结果当前也无法导出,见 roadmap)。
- 一次性音频资源:`sd.OutputStream` 每次播放新建、播完由等待线程关闭;录音流每次录音新建、停止即关闭。

## 6. 错误处理约定

- 文件加载:异常在 `MainWindow._load_file` 捕获 → `QMessageBox.critical("加载失败")` + 状态栏提示。
- 录音启动:异常捕获 → `QMessageBox.critical("录音失败")`。
- 不支持的扩展名:`load_audio` 抛 `ValueError`,同上路径呈现。
- ffmpeg 失败(未安装/转码超时):`subprocess.CalledProcessError` / `TimeoutExpired` 沿用同一路径。
