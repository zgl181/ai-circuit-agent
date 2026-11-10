# AI Agent 电路板设计

> **给 AI 一句话，产出一块板子。**
> 输入自然语言需求（如「设计一个 LED 闪烁电路，1 个 LED、1 个限流电阻、1 个轻触按钮、1 个电池座」），
> 自动产出 **BOM 元件清单 + 引脚级网表 + KiCad 8 可直接打开的原理图**。

个人课程大作业（8 周项目制） · Python 3.10+ / KiCad 8 / LLM（OpenAI 兼容接口）

---

## 效果

一句需求 → 四个产物：

| 产物 | 说明 |
|---|---|
| `outputs/design.json` | 结构化设计：`{board, components[], nets[]}`（BOM + 引脚级连接） |
| `outputs/design.net` | KiCad 标准网表（可直接给 pcbnew 做布局） |
| `outputs/design.kicad_sch` | KiCad 8 原理图，**双击即可打开** |
| `outputs/design.svg` | 原理图预览图（用于网页展示 / 自动校验） |

生成的原理图会**自动通过 `kicad-cli` 反向导出网表做连通性校验**，确认导线真的连到了引脚上，而不是"只画了好看的线"。

## 功能特性

- **自然语言 → 结构化设计**：LLM 输出固定 JSON（BOM + 网表），带自检（位号引用一致性）
- **生成真实可用的 KiCad 8 原理图**：内嵌真实符号定义，文件自包含，无需额外库表
- **正交自动布线 + 网络名标签**：先横后竖连线，网络名贴在导线中点
- **本地网页界面**：零前端框架（Python 标准库 `http.server`），输入需求 → 看 BOM / 网表 / 原理图预览
- **MongoDB 后端（可选）**：设计历史、多用户登录、LLM 结果缓存、多 Agent 共享状态；**未安装 mongod 时自动退化为内存版 mongomock，照样能跑**
- **自动校验**：`kicad-cli sch export svg`（能否打开）+ `sch export netlist`（连接是否正确）

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

该脚本校验四项，全部通过才返回退出码 0（可直接用于答辩演示或 CI）：

| 检查 | 方法 | 当前结果 |
| --- | --- | --- |
| A. 网表一致性 | `kicad-cli sch export netlist` 反导出的网表与 `design.json` 逐网络、逐引脚比对；并检测同一引脚落入多个网络（短路） | 34/34 一致，0 短路 |
| B. 电气规则 | 解析 `erc.txt`，统计 `; error` | 0 error（140 条为 `endpoint_off_grid` / `lib_symbol_issues` 提示，不影响电气） |
| C. 排版几何 | 解析 `.kicad_sch` **内嵌符号的真实引脚坐标**，检查元件重叠、标签压元件、标签互相叠字、超出 A4 图框 | 26 元件 / 108 标签，4 项均为 0 |
| D. 导线不穿模 | 逐段采样导线，检查其中段是否穿过任何元件本体（两端各跳过 4.5mm 引脚区） | 108 段导线，穿模 0 段 |

原理图为 A4 横向（`viewBox = 297×210mm`），采用**论文插画式**版面：

- **5 个虚线功能分区 + 加粗小标题**（① 电源输入、② USB-UART/CH340G、③ MCU 核心、④ 去耦与 I/O 排针、⑤ 状态指示与复位），元件只在自己的框内排布，读图不必全屏搜索。
- **主控是左右对称的 DIP-28**：左侧 pin1–14、右侧 pin28–15，引脚名按数据手册（PC6/RESET、PD0/RXD…）标注，网络标签在两侧各排成整齐一列。官方库 `ATmega328P-P` 把 23 个脚全画在右侧，这里用自包含的对称符号替换（不动本机库）。
- **LED 支路是真正的串联**：限流电阻改用横放符号，与 LED 引脚同向，`D13 → R3 → LED_D13 → D2 → GND` 画成一条干净的水平线；中间用同名网络标签保住网络名。不再是"一条线穿过电阻本体"。
- 其余符号直接嵌入 KiCad 官方库的**原始图形**（电容双板、电阻、晶振、开关、排针等），不是空白矩形框；所有导线均为短正交直线 stub（无斜线），网络标签朝元件外侧展开，位号/数值文字按真实符号高度紧贴本体。右下角标题栏未被压住。

`kicad-cli sch export svg` 可正常导出，渲染预览见 `outputs/schematic_preview.png`。

## 开发进度

- [x] W1 环境搭建（Python / KiCad / LLM 连通性自检）
- [x] W2 最小 Agent 调通学校 LLM：自然语言 → BOM JSON
- [x] W3 全链路：自然语言 → BOM + 网表 → **KiCad 8 原理图**
- [x] 加分项：本地网页界面 + MongoDB 后端（历史 / 多用户 / 缓存 / 多 Agent 共享状态）
- [ ] 进阶：自动布局布线 / DRC 检查闭环 / 多 Agent 协作 / 仿真评测
- [ ] W8 交付：设计报告 / 演示视频 / 答辩 PPT

## 已知限制

- 已完成功能分区自动布局与全正交导线；除两条 LED 支路是**实连导线**外，其余同网络引脚仍靠同名
  net label 表达电气连接（而非跨区铜线），也未生成 PCB；进阶可做全局自动布线。
- 符号映射表有限：电阻 / 电容 / LED / 开关 / 晶振 / MCU(ATmega328P) / CH340G / 连接器等。
- 为论文式版面额外内嵌 3 个**自包含符号**：`ATmega328P-DIP28`（左右各 14 脚）、
  `Device:R_H`（横放电阻）、`Device:LED_R`（右向导通 LED）。其中 LED_R 采用
  `pin1=A(阳极) / pin2=K(阴极)` 的 0603 常见编号习惯，使 `电阻→LED→GND` 正向导通；
  节点号与 `design.json` 完全一致，只改符号语义与图形方向。
- 原理图内嵌的是**自包含的简化符号副本**：为让 ERC 专注于连接完整性，`power_in` 引脚统一
  归一化为 `passive`（不建模电源驱动链，故无需额外挂 PWR_FLAG）；电源网络的真实连接关系
  由 [A] 网表一致性校验逐节点兜底。此归一化只作用于嵌入副本，不改动本机 KiCad 库。
- 已通过 `kicad-cli sch erc` 做**原理图级**电气规则校验（0 error）；尚未做 PCB DRC 与仿真评测。
- 引脚级连接的正确性依赖 LLM 输出质量（已加位号自检 + 网表反导出比对兜底）。

## 学术诚信与 AI 使用声明

- 本项目在 AI 辅助下完成，AI 参与代码编写与文档整理；关键设计决策（架构、Prompt、坐标变换、KiCad 文件格式）均经本人理解与实测验证，可口头解释。
- 未使用他人项目代码；架构思路为"自然语言 → 结构化中间产物 → CAD 文件"的通用管线。
- 参考的课程模板 / 开源资料在下方致谢注明。

## 致谢

- 课程模板工程：`<填课程提供的仓库/来源>`
- KiCad 官方符号库与 `kicad-cli` 文档
- 学校大模型平台（OpenAI 兼容接口）
