# Search Aggregator

多渠道搜索聚合服务 —— 统一 API 接口，优先级路由，自动故障切换。

一个 API Key 即可搜索 Exa、Tavily、TinyFish 等多个搜索引擎，结果自动聚合去重返回标准化格式。

## ✨ 特性

- 🔍 **多渠道聚合** — Exa / Tavily / TinyFish，YAML 配置即可扩展新渠道
- ⚡ **优先级路由** — 按 priority 顺序依次尝试，搜到结果即停，节省上游额度
- 🔄 **自动故障切换** — 渠道失败自动切换下一个，Key 级 round-robin 负载均衡
- 🛡️ **双重限制** — 频率限制（滑动窗口）+ 配额限制（累计计数）
- 👥 **用户体系** — admin/normal 角色，JWT 认证，API Key 鉴权
- 💰 **按次计费** — 余额管理，每条搜索自动扣费
- 🖥️ **Web 管理** — 可视化配置渠道、Key、用户、监控
- 📊 **调用日志** — 完整记录每次搜索，支持 CSV 导出

## 📁 项目结构

```
search-aggregator/
├── main.py                  # 启动入口
├── requirements.txt         # Python 依赖
├── venv.sh                  # 虚拟环境初始化脚本
├── start.sh                 # 服务启停脚本
├── .env.example             # 环境变量模板
├── config/
│   ├── settings.yaml        # 全局配置
│   └── channels/            # 各渠道配置（YAML，含 API Key，不提交）
│       ├── exa.yaml
│       ├── tavily.yaml
│       └── tinyfish.yaml
├── core/                    # 核心层
│   ├── models.py            # Pydantic 数据模型
│   ├── aggregator.py        # 搜索聚合引擎
│   ├── key_manager.py       # API Key 管理 + 负载均衡
│   ├── rate_limiter.py      # 频率限制 + 配额管理
│   ├── config_manager.py    # YAML 配置加载
│   ├── auth.py              # 认证（JWT + API Key）
│   ├── database.py          # SQLite 数据库
│   └── pricing.py           # 计费与余额
├── web/
│   ├── app.py               # FastAPI 应用（所有 API 端点）
│   └── templates/           # HTML 模板
│       ├── index.html       # 管理后台
│       └── login.html       # 登录页
├── data/                    # 运行时数据（不提交）
│   ├── search.db            # SQLite 数据库
│   └── logs/                # 日志文件
└── .gitignore
```

## 🚀 快速开始

### 环境要求

- **Python >= 3.10**
- **操作系统**：Linux / macOS / Windows (WSL)

### 1. 克隆项目

```bash
git clone https://github.com/yourname/search-aggregator.git
cd search-aggregator
```

### 2. 初始化环境

```bash
# 自动创建 venv + 安装依赖
source venv.sh
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入 JWT 密钥：

```bash
# 生成随机密钥
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

`.env` 完整配置项：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `JWT_SECRET` | ✅ | - | JWT 签名密钥（随机生成即可） |
| `JWT_ALGORITHM` | ❌ | `HS256` | JWT 算法 |
| `JWT_EXPIRE_HOURS` | ❌ | `24` | Token 过期时间（小时） |

### 4. 配置搜索渠道

在 `config/channels/` 下创建 YAML 文件（文件名即渠道名）：

```yaml
channel:
  name: exa                    # 渠道标识（唯一）
  display_name: Exa Search     # 显示名称
  enabled: true                # 是否启用
  method: POST                 # HTTP 方法：GET / POST
  url: https://api.exa.ai/search  # 上游 API 地址
  timeout: 5                   # 请求超时（秒），默认 5
  priority: 100                # 优先级（数字越小越优先），默认 100
  max_retries: 1               # 失败重试次数（换 Key 重试），默认 1

auth:
  type: header                 # 认证方式：header / query_param
  header_name: x-api-key      # Header 名称（type=header 时）
  header_prefix: ''            # Header 前缀（如 "Bearer "）
  keys:                        # 上游 API Key 列表（支持多个，round-robin 轮询）
    - key: YOUR_API_KEY        # 上游 API Key
      enabled: true
      labels: ["production"]   # 标签（可选）

request:
  content_type: application/json
  # POST 请求：body 模板（{query} 会被替换为搜索词）
  body_template:
    query: '{query}'
    numResults: 20
  # GET 请求：query 参数模板
  # params_template:
  #   query: '{query}'
  #   limit: '20'

response:
  results_path: results        # 结果列表在响应 JSON 中的路径（支持点号：data.hits）
  mapping:                     # 字段映射（上游字段名 -> 标准字段名）
    content: highlights        # 必填：摘要/正文
    title: title               # 可选：标题
    url: url                   # 可选：链接
    image: image               # 可选：图片

rate_limits:                   # 频率限制（per_key 维度，滑动窗口）
  per_minute: 10
  per_hour: 100
  per_day: 500
  per_month: 10000

quota:                         # 配额限制（per_channel 维度，累计计数）
  per_day: 500
  per_month: 10000
```

### 5. 启动服务

```bash
python main.py
# 或后台运行
./start.sh
```

访问 http://localhost:8830

### 6. 创建管理员账号

```bash
curl -X POST http://localhost:8830/api/admin/users \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your_password","role":"admin"}'
```

### 7. 为用户创建 API Key

```bash
# 先登录获取 JWT Token
TOKEN=$(curl -s http://localhost:8830/api/auth/login \
  -d "username=admin&password=your_password" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 为用户（user_id=1）创建 API Key
curl -X POST http://localhost:8830/api/admin/users/1/keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"quota_per_day":100,"price_per_call":0.01}'
# 返回的 key 只显示一次，务必保存！
```

## 🔑 认证体系

本项目有 **两套独立的认证机制**，作用完全不同：

| 认证方式 | 用途 | 传递方式 | 存储位置 |
|---------|------|---------|---------|
| **JWT Token** | Web 管理界面操作 | Cookie 或 `Authorization: Bearer <token>` | 内存（无持久化） |
| **API Key** | 搜索 API 调用 | `X-API-Key: sk-xxx` | SQLite（仅存哈希） |

### 两种 Key 的区别

> **这是最容易混淆的地方，请仔细区分：**

| | 上游渠道 Key（Channel Key） | 用户 API Key |
|--|---------------------------|-------------|
| **作用** | 调用上游搜索引擎（Exa/Tavily 等） | 调用本服务的搜索 API |
| **存储位置** | `config/channels/*.yaml`（本地文件） | `data/search.db` → `api_keys` 表 |
| **谁持有** | 本服务（你） | 你的用户 |
| **数量** | 每个渠道可配多个 | 每个用户可有多个 |
| **生成者** | 你在上游服务商注册获得 | 本服务自动生成（`sk-xxx` 格式） |
| **安全措施** | `.gitignore` 排除 YAML | 仅存哈希，明文只返回一次 |

**简单理解**：渠道 Key 是你的"成本"，用户 API Key 是用户的"门票"。

## 📡 API 端点

### 权限说明

- 🔒 **admin** — 仅管理员可访问
- 🔑 **login** — 登录用户可访问
- 🌐 **apikey** — 需要 X-API-Key

### 搜索 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `POST` | `/api/search` | apikey | 搜索（推荐） |
| `GET` | `/api/search` | apikey | 搜索（query 参数） |

**请求体（POST）：**

```json
{
  "query": "搜索关键词",          // 必填，最长 500 字
  "channels": ["exa", "tavily"], // 可选，不传则按优先级依次搜索所有渠道
  "max_results": 20              // 可选，范围 1-100，默认 20
}
```

**GET 参数：**

```
GET /api/search?q=AI+news&channels=exa&max_results=10
```

**响应：**

```json
{
  "query": "AI news",
  "results": [
    {
      "content": "摘要/正文片段",
      "url": "https://example.com/article",
      "title": "文章标题",
      "image": "https://example.com/image.jpg",
      "datetime": "2025-05-21"
    }
  ],
  "total": 10,
  "timestamp": "2025-05-22T10:00:00"
}
```

### 认证 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `POST` | `/api/auth/login` | 🌐 | 登录（Form 表单：username + password） |
| `POST` | `/api/auth/logout` | 🌐 | 登出 |
| `GET` | `/api/auth/me` | 🔑 | 当前用户信息 |

### 用户管理 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/admin/users` | 🔒 | 用户列表 |
| `POST` | `/api/admin/users` | 🔒 | 创建用户（username, password, role, balance） |
| `PUT` | `/api/admin/users/{id}` | 🔒 | 编辑用户 |
| `DELETE` | `/api/admin/users/{id}` | 🔒 | 删除用户 |

### API Key 管理 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `POST` | `/api/admin/users/{id}/keys` | 🔒 | 为用户创建 API Key |
| `GET` | `/api/admin/users/{id}/keys` | 🔒 | 用户的 API Key 列表（仅返回脱敏版） |
| `DELETE` | `/api/admin/keys/{id}` | 🔒 | 删除 API Key |

### 渠道管理 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/channels` | 🔒 | 渠道列表 |
| `GET` | `/api/channels/{name}` | 🔒 | 渠道详情 |
| `POST` | `/api/channels` | 🔒 | 创建渠道 |
| `PUT` | `/api/channels/{name}` | 🔒 | 更新渠道配置 |
| `DELETE` | `/api/channels/{name}` | 🔒 | 删除渠道 |
| `POST` | `/api/channels/{name}/test` | 🔒 | 测试渠道连通性 |
| `GET` | `/api/channels/{name}/keys` | 🔒 | 渠道 Key 状态（脱敏） |
| `POST` | `/api/channels/{name}/keys` | 🔒 | 添加渠道 Key |
| `DELETE` | `/api/channels/{name}/keys` | 🔒 | 删除渠道 Key |

### 监控 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/monitor/stats` | 🔒 | 系统统计 |
| `GET` | `/api/monitor/health` | 🌐 | 健康检查（无需认证） |
| `GET` | `/api/monitor/quota` | 🔒 | 配额使用情况 |
| `GET` | `/api/monitor/user-stats` | 🔒 | 用户用量统计 |

### 日志 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/logs` | 🔑 | 调用日志（admin 看所有，normal 看自己） |
| `GET` | `/api/logs/export` | 🔑 | 日志 CSV 导出 |

### 配置 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/config` | 🔒 | 系统配置 |
| `PUT` | `/api/config` | 🔒 | 更新配置 |
| `POST` | `/api/config/reload` | 🔒 | 重载配置（热更新 YAML） |

## ⚙️ 配置详解

### 全局配置（config/settings.yaml）

```yaml
app:
  name: "Search Aggregator"   # 应用名称
  version: "1.0.0"            # 版本号
  host: "0.0.0.0"             # 监听地址
  port: 8830                   # 监听端口
  debug: false                 # 调试模式（开发时开启）

defaults:
  timeout: 5                   # 单次请求超时（秒）
  max_retries: 1               # 失败重试次数
  retry_delay: 1.0            # 重试间隔（秒）

logging:
  level: "INFO"                # 日志级别：DEBUG/INFO/WARNING/ERROR
  dir: "data/logs"             # 日志目录
  rotation: "10 MB"            # 单文件最大体积
  retention: "30 days"         # 日志保留时长
```

### 响应字段映射（response.mapping）

`response.mapping` 定义如何从上游 API 的 JSON 响应中提取标准字段。

**标准字段**（固定 5 个）：

| 标准字段 | 必填 | 说明 |
|---------|------|------|
| `content` | ✅ | 摘要/正文片段（映射不到则跳过该条结果） |
| `title` | ❌ | 标题 |
| `url` | ❌ | 链接 |
| `image` | ❌ | 图片 URL |
| `datetime` | ❌ | 时间 |

**映射值**是上游 JSON 中的字段路径，支持 **点号分隔的嵌套路径**：

```yaml
# 上游响应结构：{"data": {"hits": [{"text": "...", "meta": {"url": "..."}}]}}
response:
  results_path: data.hits          # 结果列表路径
  mapping:
    content: text                   # 顶层字段
    url: meta.url                   # 嵌套字段（点号路径）
    title: meta.title.nested       # 多层嵌套
```

**特殊兼容**：如果 `content` 映射的字段不存在，会自动尝试 `snippet` 和 `content` 字段。

### 搜索渠道配置（config/channels/*.yaml）

每个 YAML 文件定义一个搜索渠道。**文件名即渠道名**（如 `exa.yaml` → 渠道名 `exa`）。

完整配置项说明见 [快速开始 > 步骤 4](#4-配置搜索渠道)。

## ⚙️ 搜索策略

### 完整请求流程

```
用户请求 "AI news"
        │
        ▼
┌─────────────────────────┐
│  API Key 验证           │ ← X-API-Key header
│  配额检查（日调用次数）   │ ← api_keys.quota_per_day
│  余额检查               │ ← api_users.balance
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  渠道筛选               │
│  ├─ enabled = true?     │
│  ├─ 有健康 Key?         │ ← key_manager.has_healthy_key()
│  └─ 日配额未满?         │ ← rate_limiter.get_quota_usage()
└────────┬────────────────┘
         │ 按 priority 排序
         ▼
┌─────────────────────────┐
│  优先级依次尝试          │
│                         │
│  渠道 A (priority=10)   │
│  ├─ Round-robin 取 Key  │ ← key_manager.get_key()
│  ├─ 频率限制检查         │ ← rate_limiter.can_call()
│  ├─ 发起 HTTP 请求      │ ← channel.timeout 秒超时
│  │   ├─ 成功 → 返回结果  │    → 记录用量 + 扣费 → 结束
│  │   └─ 失败 → 换 Key   │
│  │       └─ 重试 1 次    │ ← max_retries
│  │           └─ 仍失败   │
│  │               ↓       │
│  渠道 B (priority=50)   │ ← 同上流程
│  ├─ ...                 │
│  │   ├─ 成功 → 返回结果  │
│  │   └─ 失败 → 渠道 C   │
│  ...                    │
│  所有渠道失败 → 空结果   │
└─────────────────────────┘
```

### 优先级路由

```
请求 → 按 priority 排序 → 渠道 A（priority=10）
  ├─ 有结果 → 返回，结束
  └─ 失败/空 → 渠道 B（priority=50）
       ├─ 有结果 → 返回，结束
       └─ 失败/空 → 渠道 C（priority=100）...
所有渠道失败 → 返回空结果
```

- 每次搜索**只消耗一个渠道的额度**
- 只有当前渠道**失败或返回空结果**时才切换下一个
- 单个渠道内失败会**换 Key 重试**（`max_retries` 次）

### Key 负载均衡

同一渠道的多个 API Key 采用 **Round-Robin 轮询**：

```
Key-A → Key-B → Key-C → Key-A → ...
```

连续失败 **3 次**的 Key 会被标记为不健康并跳过，成功一次自动恢复。

### 频率限制

基于滑动窗口（内存级，per_key 维度）：

| 维度 | 说明 | 重启后 |
|------|------|--------|
| `per_minute` | 最近 60 秒调用次数 | 清零 |
| `per_hour` | 最近 3600 秒调用次数 | 清零 |
| `per_day` | 最近 86400 秒调用次数 | 清零 |
| `per_month` | 最近 30 天调用次数 | 清零 |

### 配额限制

累计计数（per_channel 维度，持久化到数据库）：

| 维度 | 说明 | 重启后 |
|------|------|--------|
| `per_day` | 当日调用总量 | 保留（从 DB 加载） |
| `per_month` | 当月调用总量 | 保留（从 DB 加载） |

跨天/跨月自动清零。

## 💰 计费系统

### 工作原理

每次搜索调用都会产生费用：

```
用户搜索 → 检查余额 → 执行搜索 → 记录用量 → 扣费
```

### 关键参数

| 参数 | 位置 | 说明 |
|------|------|------|
| `balance` | `api_users` 表 | 用户账户余额（元） |
| `price_per_call` | `api_keys` 表 | 该 Key 的单次调用价格（元） |
| `quota_per_day` | `api_keys` 表 | 该 Key 的日调用配额 |

### 扣费流程

1. 搜索前检查 `balance > 0`，否则返回 402
2. 搜索前检查日配额 `quota_per_day`，超限返回 429
3. 搜索成功后，按 `price_per_call` 扣减 `balance`
4. 记录到 `usage_logs`（含 query、channel、延迟、费用）

### 管理余额

```bash
# 充值（通过编辑用户 API）
curl -X PUT http://localhost:8830/api/admin/users/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"balance": 100.0}'
```

## 🗄️ 数据库

SQLite 数据库（`data/search.db`），包含以下表：

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| `api_users` | 用户账号 | username, password_hash, role, balance |
| `api_keys` | API Key | key_prefix, key_hash, quota_per_day, price_per_call |
| `usage_logs` | 调用日志 | query, channels, latency_ms, cost, status |

### 用户角色

| 角色 | 权限 |
|------|------|
| `admin` | 所有管理功能 + 调用日志 |
| `normal` | 仅调用日志（只看自己的） |

## 🔒 安全说明

- **JWT_SECRET** 通过环境变量配置，不硬编码
- **API Key** 仅存储哈希值（bcrypt），明文只在创建时返回一次
- **渠道 Key** 存储在 YAML 配置文件中（`.gitignore` 已排除）
- **密码** 使用 bcrypt 哈希存储
- **管理 API** 区分 admin/normal 角色权限
- **Key 列表 API** 只返回脱敏版本（前4位 + 后4位）

### 提交前检查

```bash
# 确认以下文件不会被提交
git status

# 应该被 .gitignore 排除的文件：
# .env                    # 环境变量（含 JWT_SECRET）
# config/channels/*.yaml  # 渠道配置（含上游 API Key）
# data/search.db          # 数据库（含用户密码哈希、调用日志）
# data/logs/              # 日志（可能含查询内容）
```

## 🖥️ 系统部署

### systemd 服务（推荐）

项目提供了 `search-aggregator.service` 文件：

```bash
# 复制服务文件
sudo cp search-aggregator.service /etc/systemd/system/
sudo systemctl daemon-reload

# 启动 / 停止 / 开机自启
sudo systemctl start search-aggregator
sudo systemctl enable search-aggregator
sudo systemctl status search-aggregator
```

### 手动后台运行

```bash
./start.sh           # 启动
./start.sh status    # 查看状态
./start.sh stop      # 停止
./start.sh restart   # 重启
```

### 开发模式

```bash
python main.py --reload    # 文件变更自动重载
```

## 🛠️ 扩展新渠道

接入一个新的搜索引擎只需 3 步：

### 1. 创建渠道配置

在 `config/channels/` 下新建 YAML 文件：

```yaml
# config/channels/bing.yaml
channel:
  name: bing
  display_name: Bing Search
  enabled: true
  method: GET
  url: https://api.bing.microsoft.com/v7.0/search
  timeout: 5
  priority: 200              # 比其他渠道低，作为备选
  max_retries: 1

auth:
  type: header
  header_name: Ocp-Apim-Subscription-Key
  keys:
    - key: YOUR_BING_API_KEY

request:
  params_template:
    q: '{query}'
    count: '20'

response:
  results_path: webPages.value
  mapping:
    content: snippet
    title: name
    url: url
```

### 2. 重载配置

```bash
curl -X POST http://localhost:8830/api/config/reload \
  -H "Authorization: Bearer $TOKEN"
```

### 3. 测试连通性

```bash
curl -X POST http://localhost:8830/api/channels/bing/test \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"hello world"}'
```

无需重启服务，YAML 配置 + 热重载即可生效。

## ❓ FAQ

### Q: 搜索没有结果返回？

排查步骤：

```bash
# 1. 检查渠道是否启用
curl http://localhost:8830/api/channels -H "Authorization: Bearer $TOKEN"

# 2. 检查渠道 Key 是否健康
curl http://localhost:8830/api/channels/exa/keys -H "Authorization: Bearer $TOKEN"

# 3. 测试渠道连通性
curl -X POST http://localhost:8830/api/channels/exa/test \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"test"}'

# 4. 检查配额是否用完
curl http://localhost:8830/api/monitor/quota -H "Authorization: Bearer $TOKEN"
```

### Q: 返回 401 "API Key 无效"？

- 确认使用的是 `X-API-Key` header（不是 Authorization）
- 确认 Key 是本服务生成的（`sk-xxx` 格式），不是上游的 Key
- 确认 Key 未被删除（管理界面 → 用户 → Keys）

### Q: 返回 402 "账户余额不足"？

```bash
# 查看余额
curl http://localhost:8830/api/auth/me -H "Authorization: Bearer $TOKEN"

# 充值
curl -X PUT http://localhost:8830/api/admin/users/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"balance": 100.0}'
```

### Q: 返回 429 "已达日调用配额限制"？

- 检查该 API Key 的 `quota_per_day` 设置
- 通过管理界面调整配额或创建新 Key

### Q: 某个渠道总是超时？

```bash
# 调整该渠道的超时时间
curl -X PUT http://localhost:8830/api/channels/exa \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"timeout": 10}'
```

### Q: 如何添加管理员？

```bash
# 创建 admin 角色用户
curl -X POST http://localhost:8830/api/admin/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"newadmin","password":"xxx","role":"admin"}'
```

### Q: normal 用户能做什么？

- ✅ 查看自己的调用日志
- ✅ 导出自己的日志 CSV
- ❌ 不能管理用户/渠道/Key/配置

### Q: 数据库损坏怎么恢复？

```bash
# SQLite 自带修复工具
sqlite3 data/search.db ".recover" > recovery.sql
sqlite3 data/search_new.db < recovery.sql
mv data/search.db data/search.db.bak
mv data/search_new.db data/search.db
# 重启服务
```

## 📝 License

MIT
