# Memora 技能体系

## 两个技能目录

| 目录 | 用途 | git |
|------|------|:--:|
| `shared/skill/` | 公有技能，框架自带 | ✅ 随 git 分发 |
| `local/skill/` | 本机独有技能 | ❌ 整目录被 .gitignore 排除 |

启动时两个目录自动扫描，存在即生效。AI 优先查阅 `local/skill/`。

## 目录结构

```
shared/skill/                  ← 公有
├── README.md
├── write-skill/SKILL.md
└── write-tool/SKILL.md

local/skill/                   ← 本机独有（你建的都在这里）
├── machine-profile/SKILL.md   ← 本机档案
├── user-memory/SKILL.md       ← 用户长期记忆
└── xxx/SKILL.md               ← 其他个人技能
```

## 创建新技能

1. 公有技能 → 在 `shared/skill/` 下新建（会提交到 git）
2. 本机技能 → 在 `local/skill/` 下新建（自动被忽略，不提交）

其余格式不变，参考 `write-skill/SKILL.md`。
