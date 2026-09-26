---
feature: sentence-mode
status: designed
updated: 2025-01-01
branch: feat/sentence-mode
commits: 
---

# 单句模式

## Report

## [S1] Problem

用户希望在音高曲线上快速「按句」操作：单击曲线即选中所在的一句（等价左键拖拽框选该句），播放只播这一句，并能用「上一句 / 下一句」按钮在句间跳转。句的边界必须贴近听感——静音切句、短碎合并是关键，否则会出现半句、碎句或两句粘连。

## [S2] Design

### 句分割（核心）

在现有 F0/置信度时间序列上切分，不改音频管线。

- **发声帧**：`~isnan(f0) & (confidence > 0.05)`（与绘制阈值一致）。
- **间隙切句**：相邻发声帧中心时间差 `> GAP_S`（默认 **0.45s**）则断开，前后各算不同句。
- **句范围**：段起点 = 首个发声帧时间 − `LEAD_IN`（默认 0.08s），段终点 = 末个发声帧时间 + `TAIL`（默认 0.12s），并 clamp 到 `[0, duration]`。
- **短碎合并**：段时长（发声跨度）`< MIN_SENTENCE_S`（默认 **0.25s**）时并入更近的邻段；若两端都远，则与间隙更小的一侧合并。合并后可能再次触发短段，循环直到稳定（最多 N 轮）。
- **仅静音**：整段无发声 → 句列表为空，单击不改变选择。
- 算法函数：`src/sentences.py` → `segment_sentences(times, f0, confidence, ...) -> list[tuple[float, float]]`，纯函数、可单测。

### 交互

- **常开**：左键单击（非拖拽、不落在选区边缘/底部拖拽条）时，根据点击时间选中包含该时刻的句；选区语义与拖拽框选相同（`selection_start/end`），因此播放、右键菜单、缩放到选区、循环播放全部复用。
- **拖拽**：仍为手动框选，不触发自动选句。
- **导航**：工具栏「上一句」「下一句」：相对当前选区（无选区则用上次点击/第一句）跳转并选中；首尾夹紧；选中后 `ensure_visible` 让窗口跟上。
- **播放**：已有「播放选区」路径不变；单击选句后空格/播放即播该句。
- 句列表在 `set_data` 时预计算并缓存于 PitchView；叠加录音不参与切句。

### 接口

- `PitchView.set_sentences(list[tuple[float,float]])` / 在 `set_data` 内自动 `segment_sentences`。
- `PitchView.select_sentence_at(t: float) -> bool`
- `PitchView.adjacent_sentence(delta: int) -> bool`（+1 下一句，-1 上一句），成功时更新选区并 emit `selection_changed`。
- MainWindow 工具栏按钮连接上述方法。

## [S3] Out of Scope

- 不做句级文本对齐 / 自动标点。
- 不改默认缩放、纵轴自适应、录音叠加逻辑。
- 不提供句边界拖拽微调 UI（后续可加）。
- 不在句列表中包含叠加黄线。

## Tasks

- [ ] T1: 实现 `segment_sentences` 纯函数与单元测试 — acceptance: 静音切句、短碎合并、首尾 padding 可用测试断言 (covers: S2)
- [ ] T2: PitchView 单击选句 + 上/下一句 API — acceptance: 单击选中整句，拖拽仍手动框选，adjacent 可在句间移动 (covers: S2; depends: T1)
- [ ] T3: 工具栏「上一句/下一句」按钮与播放闭环 — acceptance: 点击按钮选区跳句，播放按钮播当前句 (covers: S2; depends: T2)
- [ ] T4: 回归验证与文档 — acceptance: 旧单击标记/拖拽/录音叠加不回归，相关行为写入 docs/usage.md (covers: S2; depends: T2, T3)
