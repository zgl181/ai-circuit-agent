# AI Agent 电路板设计

> 一句话简介：输入一句自然语言需求（如「帮我设计一块 Arduino 最小系统板」），由 AI Agent 自动产出 **BOM 元器件清单 + 网表连接关系（JSON）+ KiCad 可打开的原理图**。

---

## 项目简介
- **课程**：<填课程名>
- **性质**：个人大作业（8 周项目制）
- **目标**：构建一个「会画电路板的 AI Agent」，而非手画电路板。
- **技术路线**：自然语言 → LLM(Prompt) → 结构化输出(BOM/网表 JSON) → KiCad 脚本接口 → 原理图。

## 环境要求
- Python **3.10+**
- KiCad **8.x**
- LLM API（课程统一密钥 / 学校算力平台）
- Git（GitHub / Gitee）

## 安装步骤
```bash
# 1. 克隆仓库
git clone <你的仓库地址>
cd <仓库目录>

# 2. 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置密钥（复制 .env.example 为 .env 后填写，切勿提交 .env）
cp .env.example .env
```

## 运行命令
```bash
# 最小示例：自然语言 → BOM
python examples/nl_to_bom.py --query "帮我设计一块 Arduino 最小系统板"

# MVP：自然语言 → BOM + 连接表(JSON)
python examples/mvp.py --query "..."

# 生成 KiCad 原理图
python examples/json_to_kicad.py --netlist outputs/netlist.json
```
> 以上脚本为占位，随项目推进补全；以 `examples/` 实际文件为准。

## 项目结构
```
.
├── examples/          # 可运行示例脚本
├── src/               # Agent 核心代码（Prompt、工具调用、KiCad 驱动）
├── outputs/           # 生成的 BOM / 网表 JSON / 原理图
├── tests/             # 验证脚本
├── docs/              # 设计报告、架构图
├── requirements.txt
├── .env.example       # 密钥模板（真实 .env 不提交）
└── README.md
```

## 使用说明
1. 在 `.env` 填好 LLM 密钥。
2. 运行 `examples/` 下对应脚本，输入自然语言需求。
3. 查看 `outputs/` 中的 BOM、网表 JSON 与 KiCad 原理图（`.sch`）。

## 开发进度 / 里程碑
- [ ] W1 环境搭建（脚手架跑通）
- [ ] W2 Agent 原理入门（会写工具调用）
- [ ] W3–W4 MVP（自然语言 → BOM + 连接表 JSON）
- [ ] W5–W6 KiCad 集成（JSON → 原理图，支持改需求）
- [ ] W7 迭代优化 + DRC 闭环
- [ ] W8 报告与答辩（报告 / 视频 / 答辩 PPT）

## 学术诚信与 AI 使用声明
- 本项目的代码、文案、图中，**由 AI（模型名：<填> / WorkBuddy 辅助）生成**的部分已在各文件注释与 `docs/` 中标注。
- 关键设计决策（引脚连接、Prompt 设计、架构选型）均由本人理解并能够口头解释。
- 参考的开源仓库 / 课程模板已在下方「致谢」注明出处。

## 致谢
- 课程模板工程：<填仓库/来源>
- 参考开源：<填>
