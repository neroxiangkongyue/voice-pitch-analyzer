---
feature: pitch-shift
status: delivered
updated: 2025-01-01
branch: feat/pitch-shift
commits: a29cbb8..5c93692
---

# 变调（Pitch Shift）

## Report

**What was built** — 控制栏提供 **变调(半音)** 输入框（−12…+12，步长 0.5）。改值后曲线（含黄色录音叠加）、音名轴与悬停提示按 `2^(s/12)` 立即变化；播放用 `librosa.effects.pitch_shift` 重合成、时长不变，并按（文档, 半音）缓存。参数经 `QSettings` 持久化，重启恢复。分析缓存仍基于原始音频。

**Verification** — `pixi run python -m tests.test_pitch_shift`（3 passed）；`tests.test_sentences`（7 passed，无回归）；offscreen：显示倍率 +12→×2、设置读写、播放缓存命中。

**Journey log** — 相位声码器路径保持时长，与时间轴/句边界兼容。审查子代理两次超时，以单测+冒烟+diff 人工核对收尾。基于 `feat/pitch-mode` 叠分支（本机禁止 `git worktree add` / 切回 main）。

## [S1] Problem

用户需要在听音时对整曲做变调对照练习：通过输入框设置半音偏移后，**播放音高**与**曲线音高**必须同步变化，且参数在重启后仍保留，避免每次重设。

## [S2] Design

### 参数

- 单位：**半音**（semitone），允许小数（`QDoubleSpinBox`，默认 **0**，范围 **−12 … +12**，步长 0.5）。
- 显示辅助：旁注 `×ratio` 或 `2^(s/12)` 比例，便于核对。
- 持久化：`QSettings("VoicePitchAnalyzer", "pitch-shift")`，键 `semitone`；启动时读取并应用，变更时立即写入。

### 曲线联动

- 分析仍对**原始音频**做一次 pYIN（缓存键不含变调）。
- 展示与纵轴范围使用 `f0_disp = f0 * 2^(s/12)`；无音高/置信度不变。
- 音名轴、悬停提示、可见范围检测均用变换后的 F0。
- 句分割仍基于原始置信度时间轴（变调不改时间），句边界不受影响。

### 播放联动

- 变调用 **librosa.effects.pitch_shift**（phase vocoder / 重合成，**不变速**），`n_steps=s`。
- 对「当前文档音频」按 `(id(audio), s)` 缓存变换结果，同参数重复播放不重算；变调改动后重建缓存。
- 播放选区/循环/播放头时间轴仍是原始时间（时长不变）。
- 录音叠加黄线：曲线显示同样乘比例。

### 接口

- `src/pitch_shift.py`：`semitone_ratio` / `shift_f0` / `shift_audio`。
- `PitchView.set_pitch_shift(s: float)`。
- MainWindow：控件 + `QSettings` 读写 + `_playback_audio()` 缓存。

## [S3] Out of Scope

- 不实时拖动滑杆预览音高（仅变更后下次播放生效；曲线立即更新）。
- 不做 formant 保持、合唱/和声。
- 不导出变调后的音频文件。
- 不把变调写入分析磁盘缓存。

## Tasks

- [x] T1: `pitch_shift` 纯函数 + 单测 — acceptance: ratio/shift_f0/shift_audio 时长不变、频谱移调可断言 (covers: S2)
- [x] T2: PitchView 曲线/音名/悬停随半音变化 — acceptance: set_pitch_shift 后 F0 显示与纵轴按比例变化 (covers: S2; depends: T1)
- [x] T3: 播放变调 + 缓存 — acceptance: 播放听到变调且时长不变，同参数二次播放不重复计算 (covers: S2; depends: T1)
- [x] T4: SpinBox + QSettings 持久化 — acceptance: 重启后半音保持；改值立即写入 (covers: S2; depends: T2, T3)
