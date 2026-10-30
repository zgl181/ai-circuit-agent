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

集合设计（数据库 `circuit_agent`）：

| 集合 | 用途 |
|---|---|
| `users` | 账号（加盐 SHA-256 存密码哈希） |
| `sessions` | 登录 token |
| `designs` | 设计历史（按 owner 隔离） |
| `cache` | LLM 结果缓存（按 prompt 的 SHA-256 命中） |
| `agent_state` | 多 Agent 共享状态（键值） |

## 已验证

- `kicad-cli sch export svg` 成功导出 A4 原理图（`viewBox = 297×210mm`）
- `kicad-cli sch export netlist` 反导出网表，连接与设计一致，例如：

```
/GND        <- BAT1.2, LED1.2, LED2.2
/LED1_NET   <- LED1.1, R1.2
/LED2_NET   <- LED2.1, R2.2
/SW_OUT     <- BAT1.1, SW1.1
```

## 开发进度

- [x] W1 环境搭建（Python / KiCad / LLM 连通性自检）
- [x] W2 最小 Agent 调通学校 LLM：自然语言 → BOM JSON
- [x] W3 全链路：自然语言 → BOM + 网表 → **KiCad 8 原理图**
- [x] 加分项：本地网页界面 + MongoDB 后端（历史 / 多用户 / 缓存 / 多 Agent 共享状态）
- [ ] 进阶：自动布局布线 / DRC 检查闭环 / 多 Agent 协作 / 仿真评测
- [ ] W8 交付：设计报告 / 演示视频 / 答辩 PPT

## 已知限制

- 元件目前为**网格排列**，布线为简单正交折线，版面不够"工程味"（进阶可做自动布局布线）
- 符号映射表有限：电阻 / 电容 / LED / 开关 / 晶振 / MCU(ATmega328P) / 连接器 / 电池
- 引脚级连接的正确性依赖 LLM 输出质量（已加位号自检，但未做电气规则校验 DRC）

## 学术诚信与 AI 使用声明

- 本项目在 AI 辅助下完成，AI 参与代码编写与文档整理；关键设计决策（架构、Prompt、坐标变换、KiCad 文件格式）均经本人理解与实测验证，可口头解释。
- 未使用他人项目代码；架构思路为"自然语言 → 结构化中间产物 → CAD 文件"的通用管线。
- 参考的课程模板 / 开源资料在下方致谢注明。

## 致谢

- 课程模板工程：`<填课程提供的仓库/来源>`
- KiCad 官方符号库与 `kicad-cli` 文档
- 学校大模型平台（OpenAI 兼容接口）
