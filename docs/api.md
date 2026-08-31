# 模块 API 参考(API Reference)

> 状态:v0.2.0。按模块列出公开(被跨模块使用)的函数与类。任何签名或行为变化须同步更新本文档与 [CHANGELOG.md](../CHANGELOG.md)。
> 以 `src/` 下实际代码为准,本文档描述其当前行为(包括已知怪癖,均以 ⚠ 标注)。

## audio_loader.py

### `load_audio(filepath: str) -> tuple[np.ndarray, float]`

加载音频文件,返回 `(samples, sample_rate)`。

| 参数 | 说明 |
| ---- | ---- |
| `filepath` | 音频文件路径 |

- 返回:`samples` 为 **float32 单声道** numpy 数组(多声道已平均混音);`sample_rate` 为采样率(Hz)。
- 扩展名分发(不区分大小写):
  - `.wav` / `.flac`:soundfile 直接读取,保留原始采样率;
  - `.mp3` / `.m4a` / `.aac`:调用系统 `ffmpeg` 转码为 16 kHz 单声道临时 wav(`<原路径>.tmp.wav`)后读取,超时 60 s,临时文件在 `finally` 中删除;
  - 其他扩展名:抛出 `ValueError("Unsupported audio format: <ext>")`。

## pitch_detector.py

### `extract_pitch(audio, sr, frame_length=2048, hop_length=None, fmin=50.0, fmax=500.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]`

pYIN 基频提取,返回 `(times, f0, voiced_confidence)`。

| 参数 | 默认 | 说明 |
| ---- | ---- | ---- |
| `audio` | — | float32 单声道样本 |
| `sr` | — | 采样率 |
| `frame_length` | 2048 | pYIN 帧长(样本数) |
| `hop_length` | `frame_length // 4`(512) | 帧移 |
| `fmin` | 50.0 | 基频搜索下限(Hz) |
| `fmax` | 500.0 | 基频搜索上限(Hz) |

- `times`:每帧中心时间(s);`f0`:每帧基频,未发声/未检出帧为 `NaN`;`voiced_confidence`:0–1 发声置信度。

### `detect_frequency_range(f0, _audio_length) -> tuple[float, float]`

根据检测结果估计曲线显示的频率范围 `(low, high)`。

- 取有效 F0(非 NaN)的 5%/95% 分位数,加量程 10% 的边距,夹取在 [50, 2000] Hz。
- 无有效帧时返回 `(50.0, 500.0)`。
- ⚠ 第二个参数 `_audio_length` 当前未使用(保留占位)。

## audio_recorder.py

### `class AudioRecorder(sr: int = 16000)`

麦克风录音器。属性 `is_recording: bool` 只读。

| 方法 | 行为 |
| ---- | ---- |
| `start() -> None` | 清空缓冲,打开 `sd.InputStream`(单声道、`blocksize=4096`)并启动;已在录音时静默返回 |
| `stop() -> tuple[np.ndarray, int]` | 停止并关闭流,拼接缓冲、混为单声道 float32,返回 `(samples, sr)`;未录到数据返回 `(空数组, sr)` |
| `get_default_device() -> str` *(静态)* | 返回默认输入设备名;查不到返回 `"default"` |

## audio_player.py

### `class AudioPlayer`

非阻塞音频片段播放器:基于 `sd.OutputStream` 回调喂数据,后台线程等待播放结束并释放资源;界面层用定时器轮询状态,后台侧不触碰任何 Qt 对象。

| 成员 | 行为 |
| ---- | ---- |
| `play(audio, sr, start=0.0, end=None) -> None` | **立即返回**,播放 `audio[start*sr : end*sr]` 片段(end 为 None 时播到结尾)。先停止旧播放;空片段静默返回;设备启动失败时抛出异常并复位状态。每块(512 帧,16 kHz 下约 32 ms)更新一次 `current_time` |
| `stop() -> None` | 置位停止标志(下一回调块抛 `CallbackStop`),并等待后台线程退出(至多 0.5 s);返回时资源已释放、`is_playing` 必为 False。无播放时幂等 |
| `is_playing: bool` | 是否正在播放(只读) |
| `current_time: float` | 当前播放位置,**音频时间轴上的绝对秒数**(`start + 已播放时长`;只读) |

## pitch_view.py

### `class PitchView(QGraphicsView)`

自绘音高曲线视图。

**信号**

| 信号 | 参数 | 触发时机 |
| ---- | ---- | -------- |
| `selection_changed` | `(start_time: float, end_time: float)` | 左键拖拽选区释放后 |

**公开方法**

| 方法 | 行为 |
| ---- | ---- |
| `set_data(times, f0, confidence, fmin, fmax)` | 设置分析结果与频率轴范围,全量重绘;**同时清空当前选区**(选区以秒存储,跨数据集无意义)。`times` 为空时以 `帧数 × 帧间隔` 推断时长 |
| `set_title(title: str)` | 设置视图左上角标题文本 |
| `set_zoom(zoom: float)` | 设置缩放倍率并重绘;显示时长 = 总时长 ÷ zoom |
| `set_playhead(time_val: float)` | 更新播放头位置;`time_val < 0` 隐藏播放头 |
| `setDragAccept(enabled: bool)` | 开关拖放接受 |
| `selection_start_time() -> float` / `selection_end_time() -> float` | 返回当前选区起止时间;无选区时为 `-1.0` |

**交互行为**

| 手势 | 行为 |
| ---- | ---- |
| 左键拖拽 | 框选时间区间(半透明蓝),释放后发 `selection_changed` |
| `Ctrl + 滚轮` | 缩放 +1 / −1 档,经 `parent._zoom_to_index()` 联动 |
| `Ctrl + 左键拖拽` | 水平拖拽手势缩放,吸附到最近档位 |
| 右键 | 存在选区时弹出"播放选区"菜单 |
| 拖放文件 | 转发给 `parent._load_file()` |

**模块级函数**

- `frame_dt(times) -> float`:返回相邻帧时间的中位间隔;少于 2 帧时返回 0.01。

⚠ 视图内部假定 `parent`(即 `MainWindow`)具有 `_zoom_to_index`、`_start_playback`、`_load_file` 等成员,存在双向耦合(见 [roadmap](roadmap.md) B-7)。

## main_window.py

### `class MainWindow(QMainWindow)`

主窗口。会话状态由 `AudioDocument` 列表承载:`_docs` 保存所有已导入文档(音频 + 分析结果),`_doc` 为当前激活文档;切换文档复用缓存分析结果,不重新计算。

**`AudioDocument`(dataclass)**

字段:`title`(显示标题)、`audio`(float32 单声道)、`sr`、`times` / `f0` / `confidence`(分析结果)、`fmin` / `fmax`(频率轴范围)、`path`(文件路径;录音为空串)。

**对子视图回调暴露的成员**(被 `PitchView` 直接调用,构成耦合面):

- `_zoom_to_index(idx: int)`:统一缩放入口,更新滑块、倍率标签并转发 `PitchView.set_zoom`;
- `_start_playback(start, end=None)`:非阻塞播放当前文档的 `[start, end)` 区间(end ≤ 0 时播放全部),并启动 30 ms 轮询定时器;
- `_load_file(path: str)`:已加载过的路径直接切换文档;新路径执行 加载 → 分析 → 入列 → 激活;失败弹窗。

**其他槽/事件**

| 成员 | 行为 |
| ---- | ---- |
| `_on_open_file()` | 文件对话框(多选),逐个 `_load_file` |
| `_add_document(doc)` | 追加文档到列表并激活;首次加载时移除列表占位项 |
| `_activate_document(doc)` | 停止播放、刷新视图与状态栏、缩放复位到 1×(选区由 `set_data` 重置) |
| `_on_file_selected(item)` | 点击文件列表项,切换到对应文档(复用缓存分析结果) |
| `_on_toggle_record()` | 录音开始/停止切换;停止后自动分析,以 `[录音 n]`(无路径)入列并激活 |
| `_on_toggle_play()` | 播放/停止切换;播放时播选区(无选区播全部) |
| `_stop_playback()` / `_reset_play_ui()` | 停止播放并复位按钮、播放头与位置标签 |
| `_on_play_tick()` | 30 ms 定时轮询:刷新播放头与位置标签;检测到播放结束(含自然播完)后复位界面 |
| `dragEnterEvent / dragMoveEvent / dropEvent` | 主窗口拖放导入 |

### `main()`

创建 `QApplication`、应用 Fusion 风格、显示 `MainWindow` 并进入事件循环。须经 `python -m src.main` 调用,以保证 DLL 预加载先生效(见 [design §4.1](design.md))。

## main.py

入口脚本,无公开 API。执行顺序:

1. 遍历 `sys.path` 找到 PySide6 包目录;
2. 用 `LoadLibraryExW`(标志 `0x8`)预加载 `Qt6Core.dll`、`Qt6Gui.dll`、`Qt6Widgets.dll`,并补充 `PATH` 与 `add_dll_directory`;
3. `from src.main_window import main` 并在 `__main__` 下执行。
