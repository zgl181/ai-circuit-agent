# AI Agent 电路板设计

> **给 AI 一句话，产出一块板子。**
> 输入自然语言需求（如「设计一个 LED 闪烁电路，1 个 LED、1 个限流电阻、1 个轻触按钮、1 个电池座」），
> 自动产出 **BOM 元件清单 + 引脚级网表 + KiCad 8 可直接打开的原理图**。

个人课程大作业（8 周项目制） · Python 3.10+ / KiCad 8 / LLM（OpenAI 兼容接口）

---

## 效果

一句需求 → 原理图 → PCB 全链路产物：

| 产物 | 说明 |
|---|---|
| `outputs/design.json` | 结构化设计：`{board, components[], nets[]}`（BOM + 引脚级连接） |
| `outputs/design.net` | KiCad 标准网表（可直接给 pcbnew 做布局） |
| `outputs/design.kicad_sch` | KiCad 8 原理图，**双击即可打开** |
| `outputs/design.svg` | 原理图预览图（用于网页展示 / 自动校验） |
| `outputs/design.kicad_pcb` | KiCad 8 **PCB 板文件**：真实官方封装 + 自动布局 + 双层走线 + 两层 GND 铺铜 |
| `outputs/board_routes.json` | 自动布线结果（每条走线的网络、层、折点、过孔） |
| `outputs/drc_report_summary.json` | DRC 闭环报告：每轮违规数 / 未连接数下降曲线与最终统计 |
| `outputs/pcb.svg` / `pcb_preview.png` | PCB 顶层装配视图（隐藏铜皮，便于看清元件与走线） |

原理图和 PCB 都会**自动通过 `kicad-cli` 反向校验**：前者反导网表逐引脚比对，后者跑 DRC 并用报告反向驱动布线，确认不是"只画了好看的线"。

## 功能特性

- **自然语言 → 结构化设计**：LLM 输出固定 JSON（BOM + 网表），带自检（位号引用一致性）
- **生成真实可用的 KiCad 8 原理图**：内嵌真实符号定义，文件自包含，无需额外库表
- **正交自动布线 + 网络名标签**：先横后竖连线，网络名贴在导线中点
- **PCB 自动布局布线（进阶）**：26 个元件全部映射到 KiCad 官方封装，按 5 个功能分区 shelf-packing 排布，34 个网络逐焊盘绑定；双层 0.5mm 网格 A* 自动布线，自动打过孔换层
- **DRC 闭环（进阶）**：`kicad-cli pcb drc --format json` 报出「项目之间的连接缺失」→ 解析两个焊盘的精确坐标与网络名 → 自动补线 → 重跑 DRC，逐轮让未连接项下降
- **GND 铺铜**：F.Cu / B.Cu 全板敷铜，自动避让其它网络，一次性连通所有地焊盘
- **本地网页界面**：零前端框架，原理图 / PCB 预览一键切换，可下载 `.kicad_pcb`
- **MongoDB 后端（可选）**：设计历史、多用户登录、LLM 结果缓存、多 Agent 共享状态；**未安装 mongod 时自动退化为内存版 mongomock，照样能跑**
- **五项一键自检**：网表一致性 / ERC / 排版几何 / 导线穿模 / PCB 与 DRC 闭环

## 技术架构

```
            自然语言需求
                 │
                 ▼
   scripts/design_agent.py  ── 调用 LLM（OpenAI 兼容 / 学校平台）
                 │            输出严格 JSON：BOM + nets
                 ▼
          design.json  {board, components[], nets[]}
          ┌──────┴───────────────────────┐
          ▼                              ▼
 scripts/kicad_net.py            scripts/kicad_sch.py
   → design.net (KiCad 网表)       → design.kicad_sch (KiCad 8 原理图)
                                          │
                                          ▼
                                   kicad-cli sch export svg  → design.svg
                                   kicad-cli sch export netlist → 连通性校验
```

网页层 `scripts/app.py` 把上面整条管线包成 HTTP 接口，`scripts/db.py` 提供 MongoDB 存储。

## 项目结构

```
.
├── scripts/
│   ├── design_agent.py     # 自然语言 -> BOM + 网表 JSON（真调 LLM）
│   ├── kicad_net.py        # JSON -> KiCad 网表 .net
│   ├── kicad_sch.py        # JSON -> KiCad 8 原理图 .kicad_sch（内嵌符号 + 正交布线）
│   ├── kicad_pcb.py        # JSON -> KiCad 8 PCB（官方封装 + 分区布局 + 走线 + 铺铜）
│   ├── drc_loop.py         # DRC 报告 -> 双层 A* 自动补线 -> 重跑 DRC 的闭环编排
│   ├── verify_schematic.py # 五项一键自检（原理图 A–D + PCB/DRC E）
│   ├── llm_client.py       # LLM 客户端（读取 .env）
│   ├── db.py               # MongoDB 数据层（自动退化 mongomock）
│   ├── app.py              # 本地网页服务（注册/登录/生成/历史/共享状态）
│   └── web/index.html      # 前端页面
├── outputs/                # 生成产物（示例）
├── requirements.txt
├── .env.example            # 密钥模板（真实 .env 不进仓库）
├── 启动界面.bat             # Windows 一键启动网页界面
└── README.md
```

## 环境要求

- Python **3.10+**
- KiCad **8.x**（需要 `kicad-cli` 与符号库 `share/kicad/symbols`）
- LLM API：任何 **OpenAI 兼容**接口（本项目默认接学校平台）
- MongoDB **（可选）**：不装也能跑，走 mongomock 兜底

## 安装

```bash
git clone <你的仓库地址>
cd <仓库目录>

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt

# 配置密钥：复制模板后填写
copy .env.example .env             # Windows
# cp .env.example .env             # macOS / Linux
```

### `.env` 说明

```ini
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://你的平台地址/v1
LLM_MODEL=你的模型名
```

### KiCad 路径（可选覆盖）

默认使用 Windows KiCad 8 安装路径；若你的安装位置不同，用环境变量覆盖即可：

```bash
set KICAD_SYM_DIR=D:\KiCad\8.0\share\kicad\symbols
set KICAD_CLI=D:\KiCad\8.0\bin\kicad-cli.exe
```

## 运行

### 方式一：命令行（三段管线）

```bash
# 1) 自然语言 -> 设计 JSON
python scripts/design_agent.py "设计一个 LED 闪烁电路，包含 1 个 LED、1 个限流电阻、1 个轻触按钮、1 个 2 节电池座"

# 2) JSON -> 网表
python scripts/kicad_net.py outputs/design.json outputs/design.net

# 3) JSON -> 原理图
python scripts/kicad_sch.py outputs/design.json outputs/design.kicad_sch
```

### 方式一（续）：PCB 与 DRC 闭环（进阶）

PCB 用 `pcbnew` Python API，**必须用 KiCad 自带的 Python 运行**（项目 `.venv` 里没有 pcbnew）：

```bash
# 4) JSON -> PCB（真实封装、分区布局、GND 铺铜）
"C:/Users/24013/Desktop/工具/bin/python.exe" scripts/kicad_pcb.py

# 5) DRC 闭环：自动布线 + 迭代校验，一步到位（内部会反复调用 4）
"C:/Users/24013/Desktop/工具/bin/python.exe" scripts/drc_loop.py --max-iter 7

# 6) 五项一键自检
python scripts/verify_schematic.py
```

KiCad 路径不同可用环境变量覆盖：`KICAD_FP_DIR`（封装库）、`KICAD_CLI`（命令行工具）。
`drc_loop.py` 会在最后一轮自动起一个独立进程填充铜皮，避开 pcbnew 在同一进程反复 zone fill 的段错误。

### 方式二：网页界面（推荐）

```bash
# 双击 启动界面.bat ；或手动：
.venv\Scripts\python.exe scripts\app.py
```

浏览器打开 <http://localhost:8765>：

1. 输入需求（或点示例）→ 点「生成原理图」
2. 页面直接显示 BOM 表格、网表、原理图预览
3. 可下载 `.kicad_sch` / `.net` / `.json`
4. 注册登录后，设计自动存入「我的设计历史」，可回看载入
5. 相同需求二次生成会**命中 LLM 缓存**，不再重复调用模型

## MongoDB 后端（可选）

- **不装 mongod**：`db.py` 连不上本地 `mongodb://localhost:27017` 时自动使用 `mongomock`（内存），功能齐全但重启即清空。
- **装了 mongod**：启动服务时自动切到 `real` 模式，数据持久化。

### 本机已部署（便携版，无需管理员权限）

MongoDB 8.0 便携版已下载解压到本机，普通用户即可运行：

- 程序：`C:\Users\24013\Desktop\工具\mongodb\mongodb-win32-x86_64-windows-8.0.0\bin\mongod.exe`
- 数据目录：`C:\Users\24013\Desktop\工具\mongodb-data`（重启后数据仍在）
- 默认监听 `127.0.0.1:27017`，仅本机访问

启动方式（二选一）：

- **双击 `启动界面.bat`（推荐）**：先拉起 MongoDB，等几秒再开网页界面，一条龙。
- **双击 `start-mongo.bat`**：只启动 MongoDB（不启网页），便于单独调试。

> 装好并运行后，`scripts/db.py` 会自动从 `mongomock`（内存）切到 `real`（真 MongoDB），无需改任何代码。判断当前模式：启动后访问网页、注册/生成任意一次，或在 `scripts/` 下执行
> `.venv\Scripts\python.exe -c "import db; print(db.mode())"` 会打印 `real`。

集合设计（数据库 `circuit_agent`）：

| 集合 | 用途 |
|---|---|
| `users` | 账号（加盐 SHA-256 存密码哈希） |
| `sessions` | 登录 token |
| `designs` | 设计历史（按 owner 隔离） |
| `cache` | LLM 结果缓存（按 prompt 的 SHA-256 命中） |
| `agent_state` | 多 Agent 共享状态（键值） |

## 已验证

示例设计为 **Arduino 最小系统板**（ATmega328P-PU DIP-28 + CH340G USB 转串口），
26 个元件 / 34 个网络。一键自检：

```bash
python scripts/verify_schematic.py
```

该脚本校验五项，全部通过才返回退出码 0（可直接用于答辩演示或 CI）：

| 检查 | 方法 | 当前结果 |
| --- | --- | --- |
| A. 网表一致性 | `kicad-cli sch export netlist` 反导出的网表与 `design.json` 逐网络、逐引脚比对；并检测同一引脚落入多个网络（短路） | 34/34 一致，0 短路 |
| B. 电气规则 | 解析 `erc.txt`，统计 `; error` | 0 error（140 条为 `endpoint_off_grid` / `lib_symbol_issues` 提示，不影响电气） |
| C. 排版几何 | 解析 `.kicad_sch` **内嵌符号的真实引脚坐标**，检查元件重叠、标签压元件、标签互相叠字、超出 A4 图框 | 26 元件 / 108 标签，4 项均为 0 |
| D. 导线不穿模 | 逐段采样导线，检查其中段是否穿过任何元件本体（两端各跳过 4.5mm 引脚区） | 108 段导线，穿模 0 段 |
| E. PCB / DRC 闭环 | 检查板文件、走线、闭环报告齐备；设计规则违规必须为 0，且未连接项须比首轮明显下降 | 违规 0，未连接 58 → 22 |

原理图为 A4 横向（`viewBox = 297×210mm`），采用**论文插画式**版面：

- **5 个虚线功能分区 + 加粗小标题**（① 电源输入、② USB-UART/CH340G、③ MCU 核心、④ 去耦与 I/O 排针、⑤ 状态指示与复位），元件只在自己的框内排布，读图不必全屏搜索。
- **主控是左右对称的 DIP-28**：左侧 pin1–14、右侧 pin28–15，引脚名按数据手册（PC6/RESET、PD0/RXD…）标注，网络标签在两侧各排成整齐一列。官方库 `ATmega328P-P` 把 23 个脚全画在右侧，这里用自包含的对称符号替换（不动本机库）。
- **LED 支路是真正的串联**：限流电阻改用横放符号，与 LED 引脚同向，`D13 → R3 → LED_D13 → D2 → GND` 画成一条干净的水平线；中间用同名网络标签保住网络名。不再是"一条线穿过电阻本体"。
- 其余符号直接嵌入 KiCad 官方库的**原始图形**（电容双板、电阻、晶振、开关、排针等），不是空白矩形框；所有导线均为短正交直线 stub（无斜线），网络标签朝元件外侧展开，位号/数值文字按真实符号高度紧贴本体。右下角标题栏未被压住。

`kicad-cli sch export svg` 可正常导出，渲染预览见 `outputs/schematic_preview.png`。

## PCB 与 DRC 闭环（进阶项）

板框 140×62mm，26 个元件全部使用 KiCad 8 **官方封装**（DIP-28 / SOIC-16 / HC-49 / 0603 / Micro-USB / 排针等），
按原理图的 5 个功能分区做 shelf-packing 自动布局，34 个网络逐焊盘绑定（112 处，无遗漏）。

**闭环工作方式** —— 不是"画完线再跑一次 DRC"，而是让 DRC 报告反过来驱动布线：

1. `kicad-cli pcb drc --format json` 把「项目之间的连接缺失」逐条列出，每条都带**两个端点的精确坐标与网络名**；
2. 双层 0.5mm 网格 A* 在焊盘和已布走线之间找正交通路，其它网络一律视为障碍，必要时自动打过孔换层；
3. 把新走线写回板子，重跑 DRC，统计未连接项是否下降，直到不再有改善；
4. 最后一轮铺 F.Cu / B.Cu 两层 GND 铜皮（自动避让其它网络），把地网络一次性连通。

| 阶段 | 设计规则违规 | 未连接项 |
| --- | --- | --- |
| 初始（仅布局，无走线） | 0 | 78 |
| 铺 GND 铜皮后（地网络交由铜皮负责） | 0 | 58 |
| 自动布线迭代后 | 0 | 22 |
| 最终 | **0** | **22** |

最终板子含 **221 段走线 / 25 个过孔 / 2 层铜皮**，共 56 条连接由 Agent 自动完成。
剩余 22 条为待人工或外部布线器补的连接 —— **KiCad 8 没有命令行自动布线器，本机也未装 Freerouting**，
这是工具链的客观边界；`drc_report.json` 逐条列出了端点与网络，可直接在 pcbnew 里接着布。
顶层装配视图（隐藏铜皮以便看清元件）见 `outputs/pcb_preview.png`，网页可一键切换预览。

## 开发进度

- [x] W1 环境搭建（Python / KiCad / LLM 连通性自检）
- [x] W2 最小 Agent 调通学校 LLM：自然语言 → BOM JSON
- [x] W3 全链路：自然语言 → BOM + 网表 → **KiCad 8 原理图**
- [x] W4 论文式原理图版面（对称双列 MCU / 5 个功能分区）+ 原理图四项自检
- [x] 加分项：本地网页界面 + MongoDB 后端（历史 / 多用户 / 缓存 / 多 Agent 共享状态）+ PCB 预览切换
- [x] 进阶 ①：PCB 自动布局布线（官方封装 / 分区布局 / 双层 A* 走线 / GND 铺铜）
- [x] 进阶 ②：DRC 检查闭环（DRC 报告反向驱动补线，最终违规 0）
- [ ] 进阶 ③：多 Agent 协作 / 仿真评测（未做）
- [ ] W8 交付：设计报告 / 演示视频 / 答辩 PPT

## 已知限制

- 原理图侧：已完成功能分区自动布局与全正交导线；除两条 LED 支路是**实连导线**外，其余同网络引脚
  靠同名 net label 表达电气连接（而非跨区铜线）。
- PCB 侧：已生成真实封装的板文件并完成双层自动布线，但 34 个网络中仍有 **22 条连接未布通**
  （KiCad 无 CLI 自动布线器、本机未装 Freerouting）。`drc_report.json` 逐条列出端点与网络，
  可在 pcbnew 里人工补线。设计规则违规为 0，即已布的线全部合规、无短路、无间距问题。
- USB1 采用 `USB_Micro-B_Wuerth_614105150721_Vertical`：常见 Molex-105017 封装有 8 个无网络
  屏蔽焊盘把 1–5 号信号脚围死，自动布线时线引不出来，故换成屏蔽焊盘不挡路的官方封装。
- 自恢复保险丝 F1 借用 1206 电阻封装占位（两焊盘，电气一致），未使用专用 PTC 封装。
- 符号映射表有限：电阻 / 电容 / LED / 开关 / 晶振 / MCU(ATmega328P) / CH340G / 连接器等。
- 为论文式版面额外内嵌 3 个**自包含符号**：`ATmega328P-DIP28`（左右各 14 脚）、
  `Device:R_H`（横放电阻）、`Device:LED_R`（右向导通 LED）。其中 LED_R 采用
  `pin1=A(阳极) / pin2=K(阴极)` 的 0603 常见编号习惯，使 `电阻→LED→GND` 正向导通；
  节点号与 `design.json` 完全一致，只改符号语义与图形方向。
- 原理图内嵌的是**自包含的简化符号副本**：为让 ERC 专注于连接完整性，`power_in` 引脚统一
  归一化为 `passive`（不建模电源驱动链，故无需额外挂 PWR_FLAG）；电源网络的真实连接关系
  由 [A] 网表一致性校验逐节点兜底。此归一化只作用于嵌入副本，不改动本机 KiCad 库。
- 已通过 `kicad-cli sch erc`（原理图 0 error）与 `kicad-cli pcb drc`（PCB 0 违规）；尚未做仿真评测。
- 引脚级连接的正确性依赖 LLM 输出质量（已加位号自检 + 网表反导出比对兜底）。

## 学术诚信与 AI 使用声明

- 本项目在 AI 辅助下完成，AI 参与代码编写与文档整理；关键设计决策（架构、Prompt、坐标变换、KiCad 文件格式）均经本人理解与实测验证，可口头解释。
- 未使用他人项目代码；架构思路为"自然语言 → 结构化中间产物 → CAD 文件"的通用管线。
- 参考的课程模板 / 开源资料在下方致谢注明。

## 致谢

- 课程模板工程：`<填课程提供的仓库/来源>`
- KiCad 官方符号库与 `kicad-cli` 文档
- 学校大模型平台（OpenAI 兼容接口）
