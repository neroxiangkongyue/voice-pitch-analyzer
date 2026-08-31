# 路线图(Roadmap)

> 状态:v0.2.0。记录已知问题(缺陷/技术债)与功能规划。修复或实现后从对应列表移除,并同步 [CHANGELOG.md](../CHANGELOG.md)。

## 已知问题(Bug / 技术债)

### B-4 格式白名单不一致
文件对话框过滤器含 `*.ogg`,但 `audio_loader.load_audio` 不支持,选择后必然报错。
计划:soundfile(libsndfile ≥ 1.1)已支持 ogg,可将其加入加载白名单;或从过滤器移除。

### B-5 死代码与未使用参数
- `detect_frequency_range(f0, _audio_length)` 第二参数未使用(保留占位)。

已随 v0.2.0 清理:无用的播放定时器链路、未使用的导入、`_draw_pitch_curve` 中未使用的临时 line item。

### B-6 缩放档位表重复定义
`[0.25, 0.5, 1, 2, 4, 8, 16]` 在 `main_window._zoom_steps` 与 `pitch_view` 的手势逻辑中各一份,存在失同步风险。
计划:提取为共享常量。

### B-7 `PitchView` 与 `MainWindow` 双向耦合
视图手势与选区播放直接调用 `parent._zoom_to_index`、`parent._start_playback` 等成员(v0.2.0 已将播放入口从直接操作 `parent._player` 收敛为单一方法,但仍非信号机制)。
计划:改为信号/回调接口(如 `zoom_requested`、`play_selection_requested` 信号),视图不再感知主窗口实现。

### B-8 长文件分析无进度反馈
pYIN 分析在 UI 线程同步执行,长音频加载期间界面冻结且无提示。
计划:分析移入工作线程,状态栏/进度条反馈,完成后回 UI 刷新。

## 已修复

- **B-1 播放阻塞 UI 线程**(v0.2.0)— 播放改为 `OutputStream` 回调流 + 后台线程,UI 以 30 ms 定时器轮询;
- **B-2 选区播放时播放头偏移**(v0.2.0)— `current_time` 统一为音频时间轴绝对时间,UI 层不再叠加选区起点;
- **B-3 文件列表未实现多文件管理**(v0.2.0)— `AudioDocument` 列表 + 点击切换 + 分析结果缓存,录音以 `[录音 n]` 入列;
- **播放调用不存在的 `sd.Player` API**(v0.2.0)— v0.1.0 点击"播放"必然抛 `AttributeError`(播放从未真正可用),已改用 `sd.OutputStream` 实现;
- **重绘后选区高亮丢失**(v0.2.0)— `_redraw` 清场后未重新加入选区矩形,选区高亮自首次加载数据后不可见,已修复。

## 功能规划(Feature Ideas)

按大致优先级排序,实现前先在 [requirements.md](requirements.md) 补充对应需求条目:

| 编号 | 功能 | 说明 |
| ---- | ---- | ---- |
| F-1 | 录音/分析结果导出 | 保存录音为 wav;导出 F0 为 CSV/JSON;曲线截图 |
| F-2 | 播放增强 | 播放选区循环、变速播放、音量控制 |
| F-3 | 音高参考工具 | 叠加钢琴键盘/音名刻度(如 C4/A4)、显示音符名称与音分偏差 |
| F-4 | 音域统计 | 面板显示最小/最大/中位 F0、音域范围 |
| F-5 | 多文件对比 | 同屏叠加多条曲线(依赖 B-3) |
| F-6 | 频谱图视图 | F0 曲线下方叠加频谱/语谱图 |
| F-7 | 打包发布 | PyInstaller / pixi 打包为免安装 exe;支持 macOS/Linux(platforms 与 DLL 预加载需适配) |
| F-8 | 自动化测试 | 算法层 pytest 单测(构造正弦波验证 pyin 输出);UI 层 Qt 测试 |

## 工程事项

- **许可证**:仓库尚未添加 LICENSE,发布前需确定许可证;
- **版本管理**:版本号目前仅存在于 `pixi.toml`,发布流程需统一同步(含文档页眉);
- **CI**:可引入 GitHub Actions 跑语法检查(lint)与打包冒烟(当前平台锁定 win-64)。
