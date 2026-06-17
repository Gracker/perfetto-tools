# Perfetto Tools — 设计文档

- **日期**: 2026-06-17
- **状态**: 已确认，待实现
- **作者**: chris (via ZCode brainstorming)

## 1. 背景与动机

Perfetto 的抓取方式非常分散：命令行（`adb shell perfetto`）、官方 Python 脚本
（`record_android_trace`）、网页 UI 配置、设备端系统跟踪 App，每种还因 Android 版本不同
（Android 12 前后配置文件传递方式不同）而有差异。新用户很难拼出一条可复用的抓取路径。

此外，Android 性能测试常需要两类伴随数据：**Simpleperf 的 CPU 采样**（官方生态有
`report_html.py`）和**滑动场景的 FPS/掉帧**。这些目前都要手敲命令、人工计时、人工算。

本仓库把上述能力整合到一处，提供一个"clone 下来就能用"的工具集。

## 2. 目标与非目标

### 目标
1. 归档官方 `record_android_trace` 脚本（快照 + 版本标记），让用户在本仓库即可获取。
2. 提供跨平台（Win/Mac/Linux）一键抓取 Perfetto trace 的脚本，封装 config 选择。
3. 预制 6 个常用场景的 config（`.pbtx`），覆盖启动、jank、CPU、内存等。
4. 提供 Simpleperf 抓取脚本：单独抓、以及与 Perfetto trace 同步抓。
5. 提供滑动 FPS 测试脚本：自动模拟上/下滑动，抓 trace，计算 fling 阶段的 FPS 和掉帧；
   **按多出图源分别统计**（覆盖 SurfaceView/TextureView/ImageReader/WebView/Flutter/
   视频等非标准管线），并把 TextureView 单 Buffer 覆盖计为掉帧。

### 非目标（YAGNI）
- 不抓 Linux/macOS/Windows 本机 trace（仅 Android via adb）。
- 不做 trace 可视化（用官方 ui.perfetto.dev）。
- 不做 config 的图形化编辑器（用官方 UI）。
- 不内置 adb / perfetto / simpleperf 二进制（依赖设备自带）。
- 不做基准对比 / 历史趋势 / CI 集成（后续项目）。
- fps-test 不做"自动启动 app 到指定界面"——用户手动进入界面后启动脚本。

## 3. 关键决策

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | 抓取目标平台 | 仅 Android (adb) | 覆盖 90%+ 场景，复杂度可控 |
| 2 | Perfetto 脚本实现 | Python 核心 + `.bat`/`.sh` 入口 | 核心逻辑一份，入口符合平台习惯 |
| 3 | Config 库范围 | 精选 6 个场景 | 覆盖日常，避免仓鼠库 |
| 4 | 官方脚本归档方式 | 快照 + VERSION | 离线可用、可追溯、可更新 |
| 5 | 跨平台脚本与官方关系 | 包装 `record_android_trace` | 复用官方成熟逻辑，不重造轮子 |
| 6 | 架构原则 | 一个脚本一个功能 | Unix 薄脚本，易组合易调试 |
| 7 | Simpleperf | 独立 shell 脚本 | 与 trace 同步抓时并行启动 |
| 8 | FPS 数据源 | FrameTimeline（`android.surfaceflinger.frametimeline`）+ per-layer BufferQueue（`frame_slice`） | FrameTimeline 覆盖其追踪到的 app surface 层；其余出图源用 per-layer BufferQueue 补齐并去重（见 11） |
| 9 | fling 窗口界定 | 优先从结构化 input 事件（`android.input.inputevent`）提取；user build 无 input 时降级到设备时钟滑动标记 | 精确区分按压/触离；结构化 input 仅 debuggable build 可用 |
| 10 | 滑动模拟 | `adb shell input swipe` | 跨设备通用、零依赖 |
| 11 | FPS 多出图源 | 按 layer/surface 分别统计 FrameTimeline + BufferQueue，再汇总；TextureView 单 Buffer 覆盖计为掉帧 | SurfaceView/TextureView/ImageReader/WebView/Flutter/视频不走标准管线，混成一个数字会误判（见 §5.1、§5.2） |

> **实现演进（基于真机 trace 验证，2026-06-17）**：用 `SmartPerfetto/test-traces/`
> 下 6 个真机滑动 trace 实测后，对决策 #8、#9 做了两处修订：
> - **#8（数据源）**：6 个 trace 的 `frame_slice`（per-layer BufferQueue）**全部为空**，
>   TextureView 单 Buffer 覆盖检测（`detect_overwrite_drops`/`buffer_events_to_frames`）
>   无数据可测，保留为已测库但**未接入** `analyze_trace`。当前 FPS 完全来自
>   FrameTimeline（`actual_frame_timeline_slice`），按 layer_name 分源。
> - **#9（窗口界定）**：原定义"ACTION_UP → 下一次 ACTION_DOWN"对单次手势 trace
>   产出 **0 窗口**（UP 后再无 DOWN），且丢弃最后一次手势的惯性尾巴。修订为
>   **DOWN → 下一次 DOWN**（整段交互），并按每次手势拆成**三个 FPS 相位**：
>   overall（按压+惯性）、press（DOWN→UP，按压响应）、fling（UP→next DOWN，惯性流畅度）。
>   tier-1 的时间戳从 `event_time` 改为 `COALESCE(event_time, dispatch_ts)`（API 36
>   user build 上 `event_time` 全为 NULL）。详见 `fps-test/README.md` 与
>   `compute_fps.py` 模块 docstring。

## 4. 仓库结构

```
perfetto-tools/
├── README.md                          # 总入口 + 导航
├── LICENSE
├── .gitignore
│
├── official/                          # 块1：官方脚本快照归档
│   ├── README.md                      # 来源、VERSION、更新方法
│   ├── VERSION                        # perfetto commit + 抓取日期
│   └── record_android_trace           # 官方 Python 脚本（可执行）
│
├── capture/                           # 块2：Perfetto trace 抓取
│   ├── README.md
│   ├── perfetto_capture.py            # 核心：config 解析 + 调 official/
│   ├── capture.bat                    # Windows 入口
│   └── capture.sh                     # Mac/Linux 入口
│
├── simpleperf/                        # 块4：Simpleperf 抓取（shell）
│   ├── README.md
│   ├── simpleperf_only.sh             # 单独抓 simpleperf
│   └── simpleperf_with_trace.sh       # simpleperf + Perfetto trace 同步抓
│
├── fps-test/                          # 块5：滑动 FPS 测试
│   ├── README.md
│   ├── run_fps_test.sh                # 主流程：滑动+抓trace+算FPS（多出图源）
│   ├── compute_fps.py                 # trace_processor SQL：FrameTimeline+BufferQueue
│   ├── dump_gfxinfo.sh                # 辅助：dumpsys gfxinfo/SurfaceFlinger 交叉验证
│   └── swipe_pattern.txt              # 滑动参数（便于调参）
│
├── configs/                           # 块3：预制 Config 库
│   ├── README.md
│   ├── 00_general.pbtx                # 通用默认
│   ├── 01_app_startup.pbtx            # 应用启动 / 冷启
│   ├── 02_jank_frame.pbtx             # 滑动/jank（含 input + FrameTimeline）
│   ├── 03_cpu_sched.pbtx              # CPU/调度
│   ├── 04_memory.pbtx                 # 内存
│   └── 05_full.pbtx                   # 全量调试
│
├── traces/                            # 输出目录（.gitignore）
│
├── tests/                             # 纯逻辑单测（pytest）
│
└── docs/
    ├── spike-notes.md                 # Task 0 真机探查结论（schema/字段名定稿）
    └── superpowers/
        ├── specs/2026-06-17-perfetto-tools-design.md
        └── plans/2026-06-17-perfetto-tools.md
```

## 5. 各组件详细设计

### 块1：`official/` — 官方脚本归档

- `record_android_trace`：从
  `https://raw.githubusercontent.com/google/perfetto/master/tools/record_android_trace`
  抓取的快照，保留可执行权限。
- `VERSION`：记录 perfetto 仓库的 commit hash + 抓取日期。
- `README.md`：说明来源 URL、当前版本、更新方法（重新下载 + 更新 VERSION）。
- 这个脚本既是"归档"，也是块2、块5的运行时依赖。

### 块2：`capture/` — Perfetto trace 抓取

**职责单一**：选 config → 调 official 脚本生成 trace。不做 FPS、不做 simpleperf。

**`perfetto_capture.py`**（核心逻辑，被两个入口调用）：
1. 参数解析：`-c/--config <name>`、`-t/--time <秒>`、`-o/--output`、
   `-s/--serial`（多设备）、`--list-configs`、`-h/--help`。
2. config 解析：用户给短名（如 `jank`），在 `configs/` 模糊匹配到
   `02_jank_frame.pbtx`；未给则列出所有可选。
3. 设备检查：`adb devices`，多设备时要求 `--serial`。
4. 时长处理：`record_android_trace` 在传 `-c/--config` 时会**忽略** `-t`
   （`-t/-b/-a` 仅在不带 `-c` 时生效）。因此 `--time` 不透传为 `-t`，而是把
   config 里的 `duration_ms` 改写后写入临时 config（`apply_duration()`，纯函数、
   可单测），用临时文件作为 `-c`。未给 `--time` 时沿用 config 自带 `duration_ms`。
5. 调用 official：`python3 official/record_android_trace -c <临时config>
   -o <output> [-s <serial>] [--no-open]`，转发 stdout/stderr，结束后删除临时文件。
6. 输出路径默认 `./traces/<timestamp>_<config>.perfetto-trace`。

**`capture.bat`** / **`capture.sh`**：薄入口，定位 `perfetto_capture.py` 和
`official/`，转发参数。`.sh` 设可执行权限。

### 块3：`configs/` — 预制 Config

6 个场景，命名带数字前缀（排序 + 模糊匹配友好）：

| 文件 | 场景 | 关键数据源 | 备注 |
|---|---|---|---|
| `00_general.pbtx` | 通用默认 | sched/freq/idle + am/wm/gfx/view + binder + memory | 最常用 |
| `01_app_startup.pbtx` | 应用启动 | + am/wm 详细、view、启动相关 atrace | 冷启分析 |
| `02_jank_frame.pbtx` | 滑动/jank | **frametimeline** 数据源 + **android.input** + gfx/view atrace | **块5默认复用** |
| `03_cpu_sched.pbtx` | CPU/调度 | sched 详细、freq、idle、CPU 采样 | 调度分析 |
| `04_memory.pbtx` | 内存 | 内存计数器、lmk、(可选)heap | 内存分析 |
| `05_full.pbtx` | 全量 | 上述全部，buffer 拉大 | 调试用 |

每个 `.pbtx` 为文本 protobuf，可读可改。`configs/README.md` 说明用途、适用
Android 版本、trace 大小量级。

**ATrace 写法**：atrace 类别通过 `linux.ftrace` 数据源的 `atrace_categories` /
`atrace_apps` 字段配置，**不存在**独立的 `android.atrace` 数据源（用了会导致
config 解析失败）。

`02_jank_frame.pbtx` 关键，必须额外包含两个独立数据源：
- `android.surfaceflinger.frametimeline`：权威的帧时序/jank 数据（供块5算 FPS），
  需要 Android 12 / API 31+。仅靠 `gfx` atrace 拿不到 FrameTimeline 表。
- `android.input.inputevent`：结构化 input 事件（供块5精确界定 fling 窗口），
  **仅在 debuggable/userdebug/eng build 上记录**；user build 上块5自动降级到
  设备时钟滑动标记。

### 块4：`simpleperf/` — Simpleperf 抓取

两个独立 shell 脚本，互不依赖。

**`simpleperf_only.sh [pkg] [duration]`**：
1. `adb shell pm path <pkg>` / `pidof` 定位进程。
2. `adb shell simpleperf record -p <pid> -g --duration <duration>
   -o /data/local/tmp/perf.data`。
3. `adb pull` 到本地 `./traces/`。
4. 提示用 `report_html.py` 查看（给出命令，不强制执行）。

**`simpleperf_with_trace.sh [pkg] [duration]`**：
1. 后台启动 `simpleperf record`（同上）。
2. 同时调 `../capture/capture.sh` 抓 Perfetto trace（用 `03_cpu_sched`）。
3. `wait` 等 simpleperf 结束。
4. pull perf.data，输出两个文件路径。
5. README 注明：Perfetto 自带 `linux.perf` 数据源可作为更优替代（避免双重
   perf_event_open 开销），本脚本面向需要 simpleperf 原生输出格式的场景。

### 块5：`fps-test/` — 滑动 FPS 测试

一个主脚本 + 一个算 FPS 的 Python 工具 + 一个辅助 dump 脚本。

**`run_fps_test.sh [duration_sec] [package]`**（`package` 可选，给了就跑 §5.3 的
gfxinfo/SF 交叉验证；不自动启动 app）：
1. 确认设备连接；目标界面由用户手动进入（不自动启动）。
2. 后台启动 Perfetto trace 抓取（用 `02_jank_frame.pbtx`，默认时长 ~12s 留足余量）
   ——复用块2。启动后 sleep ~2s 等 tracer 真正就绪再开始滑动。
3. 执行滑动序列：上滑 3 下、下滑 3 下（`adb shell input swipe`，每下间 sleep）。
   每下滑动后用**设备时钟**（`adb shell date +%s%N`，设备 realtime 纳秒）记录
   fling 窗口，写入 swipe.log 作为降级用。
4. 等 trace 结束 + pull。
5. 调 `compute_fps.py <trace>`。
6. 输出报告。

**`compute_fps.py <trace_path>`**：
1. 加载 trace 到 trace_processor。
2. **主路径**：从结构化 input 事件（`android.input` stdlib，由
   `android.input.inputevent` 数据源填充）提取 `ACTION_UP` → 下一次 `ACTION_DOWN`
   = fling 阶段；frame 与窗口都在 trace time。
   **降级路径**：user build 无 input 时，用 swipe.log 的设备时钟窗口，并把 frame
   的 ts 经 `TO_REALTIME()` 转成设备 realtime 一并比较（同一时钟域，避免宿主/设备
   时钟错配）。确切表名/列名/`TO_REALTIME` 行为在 **Task 0 spike** 探查确认。
3. **多出图源统计**（关键，见下）：FrameTimeline 给出它追踪到的 app surface 帧，但
   **统计不到** SurfaceView、SurfaceTexture/TextureView、ImageReader、WebView、
   Flutter、视频播放等不走标准管线的独立出图源。因此 FPS 按"每个活跃出图源
   （layer/surface）"分别统计：FrameTimeline 覆盖的 layer 用它，其余 layer 用
   per-layer BufferQueue 补齐并**去重**，最后再汇总。
4. 对每个 fling 窗口、每个出图源，算：帧数、FPS、**掉帧**、**jank**、掉帧率。
   注意区分两个概念：**掉帧（dropped）**=从未上屏（FrameTimeline `present_type =
   'Dropped Frame'`，或 TextureView 单 Buffer 覆盖），不计入 FPS；**jank**=上了屏
   但晚了（`jank_type != None`），仍计入 FPS，只作为质量信号单列。
5. 汇总：**各出图源各自的 FPS**为主指标。"整体"只是所有源的出帧吞吐之和（60fps 列表
   叠 30fps 视频不是 90fps 屏幕刷新率），不作为屏幕 FPS。
6. 输出到 stdout 和 `traces/<name>.fps_report.txt`。

#### 5.1 多出图源（出图源 = layer / BufferQueue）

一帧"出图"最终都体现为某个 surface 的 BufferQueue 操作（dequeue → queue →
acquire → latch → present）。不同渲染路径走不同 surface：

| 出图源 | 典型场景 | FrameTimeline 能否覆盖 |
|---|---|---|
| ViewRootImpl 主 surface | 普通 View/RecyclerView 滑动 | ✅ 能（标准管线） |
| SurfaceView | 游戏、相机预览、部分视频 | ❌ 独立 BufferQueue |
| TextureView / SurfaceTexture | 视频、Flutter、自定义渲染 | ❌（且单 Buffer，见 5.2） |
| ImageReader | 截图/编码消费端 | ❌ |
| WebView | 网页内容 | ❌ 独立 surface |

**做法**：
- FrameTimeline（`actual_frame_timeline_slice`）按 `layer_name` **分 surface** 给出
  每个 app surface 的帧（含精确 jank/drop），每个 layer 自成一个出图源。
- 非管线 layer 用 **per-layer BufferQueue** 补齐。优先用 stdlib `frame_slice`
  （`layer_name` + `queue_to_acquire_time` / `acquire_to_latch_time` /
  `latch_to_present_time`，已成对配好生产/消费），而非在 `slice` 里裸搜
  `queue/acquire/latch`——后者没有 buffer identity，易误判。
- **去重**：BufferQueue 统计要**排除** FrameTimeline 已覆盖的 layer，避免同一 surface
  双重计数。
确切数据源名 / 表名 / layer 名归组方式在 **Task 0 spike** 确认。报告需**分出图源**
列出，避免把多源混成一个数字而误判。

#### 5.2 TextureView 单 Buffer 覆盖（视为掉帧）

TextureView/SurfaceTexture 当前多为**单 Buffer** 机制：生产者把新内容写入 buffer
后，如果消费者（SurfaceFlinger/GL）**尚未消费（latch/acquire）**，这块 buffer 就被
下一帧**覆盖**——上一帧从未上屏。我们把这种"buffer 未被消费即被覆盖"判定为**掉帧/
卡顿**。

**检测**：在该 layer 的 BufferQueue 事件序列上，若出现一次 `queue`/buffer 更新，而
其紧邻的上一次 `queue` 之后**没有**对应的 `acquire`/`latch`（消费）事件，则计一次
覆盖掉帧。这部分是纯序列逻辑，单测可用合成的事件序列覆盖（见 Task 7）。

⚠️ **仅对确认为单 Buffer 的 layer 适用**：普通双/三 Buffer layer 上"两次 queue 之间
没有 acquire"并不能证明被覆盖（消费者可能稍后一次性 acquire 多个 buffer）。因此覆盖
检测**只对 Task 0 spike 在真机 TextureView 场景确认为单 Buffer/async-drop 的 layer
启用**，不能对所有 layer 盲套。具体事件名 / 单 Buffer 判定在 spike 确认。

#### 5.3 辅助：`dumpsys` 交叉验证（独立于 trace）

除 trace 外，再提供一个**独立的辅助脚本** `fps-test/dump_gfxinfo.sh`，在 fps 测试
前后 dump 两类系统统计，与 trace 算出的 per-source 数字相互印证（不是主指标，是旁证）：

- **`dumpsys gfxinfo <pkg> framestats`**：应用渲染管线的整进程帧统计——总帧数、
  Janky frames、50/90/95/99 分位、以及每帧原始时间戳 CSV。测试前 `reset`、测试后 dump。
- **`dumpsys SurfaceFlinger --latency <layer>`**：某个 layer 的逐帧
  (desired / actual present / ready) 时间戳，可据此独立算该 layer 的 FPS。测试前
  `--latency-clear`、测试后按 app layer dump。

定位：**额外辅助**，与块5 主流程解耦——既可单独跑，也可给 `run_fps_test.sh` 传可选
第二位置参数 `<package>`，在滑动前后自动 reset/dump。gfxinfo 是整进程口径、
SurfaceFlinger --latency 是单 layer 口径，二者与 trace 的 per-source 口径不同，**用于
交叉印证而非替代**。

**滑动节奏**：上下各 3 下（共 6 下），每下 swipe duration ~400ms + sleep
~600ms ≈ 1s/下，约 6s 滑动；加 2s 就绪等待与 adb 往返，trace 默认取 12s 留足
余量。可在 `swipe_pattern.txt` 调参。

## 6. 数据流（块5为例）

```
run_fps_test.sh 12 com.example.app      # 时长在前，包名(可选)在后
  │
  ├─[后台] capture.sh --config jank --time 12 --output traces/xxx.perfetto-trace
  │            └─ (改写 duration_ms 的临时 config) → record_android_trace → adb perfetto
  │
  ├─ sleep 2  (等 tracer 就绪)
  │
  ├─[前台] for i in 1..3: adb shell input swipe  ↑ (上滑)
  │        每下后用 adb shell date +%s%N 记录设备时钟窗口 → swipe.log
  │        for i in 1..3: adb shell input swipe  ↓ (下滑)
  │
  ├─ wait trace 结束 + pull
  │
  └─ compute_fps.py traces/xxx.perfetto-trace --swipe-log swipe.log
         ├─ trace_processor 加载
         ├─ 提取结构化 input 事件 → fling 时间窗（无则降级到设备时钟窗口）
         ├─ FrameTimeline(actual_frame_timeline_slice) 按 layer 分源
         ├─ 非管线 layer 用 frame_slice 补齐（去重）+ 单 Buffer 覆盖检测
         └─ 输出（分出图源）:
              app-pipeline        fps=59.1 dropped=2 janky=14
              SurfaceView[video]  fps=29.8 dropped=2 janky=4
              TextureView[...]    dropped=8(单Buffer覆盖)
```

## 7. 关键风险与对策

| 风险 | 对策 |
|---|---|
| trace_processor 依赖（~30MB） | `compute_fps.py` 首次运行检测；缺则提示 `pip install perfetto` 或下载 `trace_processor_shell`。README 写清两种方式。 |
| 结构化 input 仅 debuggable build 可用 | `02_jank_frame.pbtx` 带 `android.input.inputevent`；compute_fps.py 拿不到 input 时**降级**到设备时钟滑动标记并打印警告，不直接失败。承诺"精确区分按压/触离"仅在 debuggable build 成立。 |
| fallback 窗口与 frame 的时钟域错配 | 滑动标记用**设备** `date +%s%N`（设备 realtime），降级时把 frame ts 经 `TO_REALTIME()` 转到同一时钟；不混用宿主时钟。也顺带规避 macOS BSD `date` 无 `%N` 的问题。 |
| FrameTimeline 需 Android 12+ | 旧设备 FPS 数据不全；README 注明最低 API 31，并在报告为空时给清晰提示。 |
| 漏统计非标准管线出图源 | FrameTimeline 之外，按 per-layer BufferQueue events 补齐 SurfaceView/TextureView/ImageReader/WebView/Flutter/视频；报告**分出图源**，不混成单一数字（§5.1）。 |
| TextureView 单 Buffer 覆盖被漏判 | 在 layer 的 BufferQueue 序列上检测"queue 后无 acquire/latch 即被下次 queue 覆盖"，计为掉帧（§5.2）；纯序列逻辑单测覆盖。 |
| Perfetto config schema 漂移 | 关键数据源名/SQL 表列在 **Task 0 spike** 用真机确认后才定稿，不靠记忆。 |
| adb input swipe 节奏不精确 | 参数可调（swipe_pattern.txt）；对"上下各 3 下"节奏足够。 |
| simpleperf 需 debuggable app 或 root | 脚本检测，不满足给清晰报错；`with_trace` 用 trap 保证失败也清理远端 perf.data。 |
| 官方脚本接口变更 | VERSION 锁定到 commit 级 URL（非 master 浮动）；升级时整体替换 + 重新测试。注意"离线可用"有限：首次仍可能需下载/缓存 tracebox。 |
| 跨平台路径差异 | Python 核心用 `pathlib`；入口脚本做平台分发。 |

## 8. 错误处理

- `adb` 不在 PATH：报错 + 安装提示。
- 无设备 / 未授权：报错并列出 `adb devices` 输出。
- config 名不匹配：列出所有可用 config。
- 官方脚本执行失败：转发 stderr，不吞错误。
- Python < 3.9：报错（项目用 dataclasses / future annotations / removesuffix）。
- trace_processor 缺失：提示安装方式。
- simpleperf 目标进程不可调试：报错说明原因。

## 9. 测试策略

脚本类项目，手工验收 + 轻量自动化。注意：纯逻辑单测（config 解析、`apply_duration`
时长改写、FPS 数学、swipe 解析）**只覆盖低风险逻辑**；真正易错的是
config schema 合法性、`record_android_trace` flag 契约、SQL 表/列存在性、input
可用性——这些靠 **Task 0 spike** + 手工验收兜底，不能只看单测全绿。

- **Task 0 spike**：真机确认 config 解析、frame/input 表列名、`TO_REALTIME`，落
  `docs/spike-notes.md`。块3/块5 必须与之对齐后才算完成。
- **块1**：`record_android_trace --help` 能跑。
- **块2**：`capture.sh --list-configs` 列出 6 个；`--config jank` 解析到
  `02_jank_frame.pbtx`；`--time` 改写临时 config 的 `duration_ms`；缺设备时报错清晰。
- **块3**：每个 `.pbtx` 能通过 `perfetto --txt` 解析（README 给校验命令）；
  确认无 `android.atrace`、frametimeline/input 数据源拼写正确。
- **块4**：在 debuggable app 上 record + pull 成功；与 trace 同步抓不互锁；
  capture 失败时 trap 仍清理远端 perf.data。
- **块5**：端到端在真机跑通，FPS 报告非空且数值合理（手动验收）。
- 所有 shell 脚本过 `shellcheck`。

## 10. 实现顺序

**Task 0 spike 先行**（de-risk 核心假设），之后自底向上，每步可独立验收：

0. **真机 spike**：确认 config 解析 + trace_processor schema（见 §9 / Task 0）。
   无设备则阻塞并告知用户，不在未验证假设上推进。
1. 仓库骨架（目录、LICENSE、.gitignore、各 README 占位）
2. 块3 configs（其他块都依赖它；数据源/字段按 spike 结果定稿）
3. 块1 official 归档（块2、5 依赖它）
4. 块2 capture（核心入口）
5. 块4 simpleperf（独立，可并行）
6. 块5 fps-test（依赖块2、3；FPS SQL 按 spike 结果定稿）

## 11. 参考资料

- Perfetto trace 抓取：https://perfetto.dev/docs/getting-started/start-using-perfetto
- Android 实操指南：https://www.androidperformance.com/2024/05/21/Android-Perfetto-02-how-to-get-perfetto/
- FrameTimeline / Jank：https://perfetto.dev/docs/data-sources/frametimeline
- TraceConfig proto（atrace 在 ftrace_config 内）：https://perfetto.dev/docs/reference/trace-config-proto
- PerfettoSQL 标准库（android.frames / android.input）：https://perfetto.dev/docs/analysis/stdlib-docs
- 时钟同步 / CLOCK_BOOTTIME 与 realtime：https://perfetto.dev/docs/concepts/clock-sync
- Perfetto CPU profiling（linux.perf）：https://perfetto.dev/docs/getting-started/cpu-profiling
- Simpleperf README：https://android.googlesource.com/platform/system/extras/+/master/simpleperf/doc/README.md
- dumpsys gfxinfo / SurfaceFlinger（辅助交叉验证）：https://developer.android.com/topic/performance/measuring-performance
- 测试用 Demo APK（朋友圈滑动性能测试）：https://github.com/Gracker/Friends-Circle-Demo-Apks-For-Power-and-Performance-Test
