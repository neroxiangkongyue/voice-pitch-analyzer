---
feature: async-analysis
status: in-progress
updated: 2025-01-01
branch: feat/async-analysis
commits: 
---

# 分析移到后台线程

## Report

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
- [ ] T1: Worker + 线程生命周期 (covers: S2)
- [ ] T2: 分析中禁用与状态栏 (covers: S2; depends: T1)
- [ ] T3: 加载状态栏 i/n (covers: S2)
- [ ] T4: 冒烟与文档 (covers: S2; depends: T1, T2)
