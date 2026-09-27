---
feature: async-analysis
status: delivered
updated: 2025-01-01
branch: feat/async-analysis
commits: 69cb64e..a93ed77
---

# 分析移到后台线程

## Report

**What was built** — 停止录音后的 pYIN 改为后台 `_RecordAnalyzeWorker`，UI 不卡顿；分析中禁用录音并提示，完成后叠加/新文档。加载状态栏显示 i/n。

**Verification** — 语法 OK；offscreen 异步完成恢复按钮并叠加 +1。

**Journey log** — 文件加载本已异步；录音停止才是 UI 卡点。

## [S1] Problem

停止录音后 pYIN 在 UI 线程同步分析会卡界面；文件加载已有工作线程，但录音分析与进度文案未统一。

## [S2] Design

### 录音分析异步
- `_RecordAnalyzeWorker` 在 QThread 中跑 `extract_pitch`，Signal 回 UI。
- 分析中禁用录音按钮并提示；完成后走叠加/新文档。
- 同时仅一个分析任务。

### 统一进度反馈
- 加载：`加载 i/n: 文件名`；录音分析：`正在分析录音…`。

## [S3] Out of Scope
- 按帧百分比；改加载数据路径；播放/变调进线程。

## Tasks
- [x] T1: Worker + 线程生命周期 (covers: S2)
- [x] T2: 分析中禁用与状态栏 (covers: S2; depends: T1)
- [x] T3: 加载状态栏 i/n (covers: S2)
- [x] T4: 冒烟与文档 (covers: S2; depends: T1, T2)
